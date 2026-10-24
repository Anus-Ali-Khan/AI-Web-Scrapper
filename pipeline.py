"""Orchestrates everything: intent -> discovery stream -> scrape -> filter -> save.
Headless by default, resumable, survives blocks via cooldown + browser restart."""
import csv
import difflib
import json
import random
import re
import time
from collections import deque
from pathlib import Path

from analyze import enrich_with_llm, passes_basic_filters, passes_country_filter, passes_llm_filters
from country import resolve_country
from browser import Browser
from discover import _search_url, extract_results, iter_candidates, verify_country_via_search
from locate import locate_country
from intent import SearchIntent
from insta_profile import scrape_profile

CSV_FIELDS = ["username", "full_name", "url", "followers", "following", "posts", "niche", "summary",
              "bio", "email", "account_type", "gender", "location", "country", "country_match",
              "country_evidence", "llm_status", "match_score", "gender_match",
              "relevance", "profile_image"]


class _Holder:
    """Owns the current Browser so it can be swapped (restart) while generators keep working."""
    def __init__(self, headless, proxy):
        self.headless, self.proxy = headless, proxy
        self.browser = Browser(headless=headless, proxy=proxy)

    def get(self):
        if not self.browser.alive():
            self.restart()
        return self.browser

    def restart(self):
        self.browser.close()
        self.browser = Browser(headless=self.headless, proxy=self.proxy)

    def close(self):
        self.browser.close()


def _slug(intent: SearchIntent) -> str:
    base = "-".join(filter(None, [intent.niche, intent.country, intent.gender,
                                  *intent.hashtags, str(intent.count)]))
    return re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")[:80] or "search"


def _write_csv(path: Path, rows: list[dict]):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def _find_single_profile(intent, holder, use_llm, report):
    """'find this profile X': try it as a handle, then fall back to a web search by name."""
    query = intent.profile_query
    candidates = []
    if re.fullmatch(r"[A-Za-z0-9._]{2,30}", query):
        candidates.append(query)
    html = holder.get().get_html(_search_url("bing", f'site:instagram.com "{query}"', 0), 2, 4)
    snippets = {}
    for u, text in extract_results(html):
        snippets[u.lower()] = text
        if u.lower() not in [c.lower() for c in candidates]:
            candidates.append(u)

    results = []
    for i, username in enumerate(candidates[:5], start=1):
        report(i - 1, min(len(candidates), 5), f"Reading @{username}...")
        profile = scrape_profile(holder.get(), username)
        if profile:
            if use_llm:
                profile = enrich_with_llm(profile, intent)
            # country is an OUTPUT here: bio -> search snippet -> web search about the person
            profile["country"], profile["country_evidence"] = locate_country(
                profile, snippets.get(username.lower(), ""), holder.get(), use_web=True, use_llm=use_llm)
            profile.pop("raw_description", None)
            profile.pop("_llm_country", None)
            q = query.lower().replace(" ", "")
            profile["match_score"] = round(max(
                difflib.SequenceMatcher(None, q, username.lower()).ratio(),
                difflib.SequenceMatcher(None, q, (profile["full_name"] or "").lower().replace(" ", "")).ratio()), 2)
            results.append(profile)
        time.sleep(random.uniform(3, 6))
    results.sort(key=lambda r: r["match_score"], reverse=True)
    return results


def run_search(intent: SearchIntent, headless: bool = True, use_llm: bool = True,
               out_dir: str = "results", proxy: str | None = None, on_progress=None,
               delay: tuple = (4, 9), cooldown_seconds: int = 300, max_cooldowns: int = 3,
               restart_every: int = 40, verify_country: bool = True, find_country: bool = False):
    """Returns (accepted_profiles, stats). Results are saved as you go; re-running the same
    prompt resumes where it stopped."""
    report = on_progress or (lambda d, t, m: print(f"[{d}/{t}] {m}"))
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stats = {"checked": 0, "rejected": 0, "unreadable": 0, "stopped": "target reached", "csv": None}

    holder = _Holder(headless, proxy)
    try:
        if intent.mode == "profile":
            results = _find_single_profile(intent, holder, use_llm, report)
            stats["csv"] = str(out / f"profile-{re.sub(r'[^a-z0-9]+', '-', intent.profile_query.lower())}.csv")
            _write_csv(Path(stats["csv"]), results)
            return results, stats

        slug = _slug(intent)
        jsonl, seen_file, csv_path = out / f"{slug}.jsonl", out / f"{slug}.seen", out / f"{slug}.csv"
        accepted = [json.loads(l) for l in jsonl.read_text(encoding="utf-8").splitlines()] if jsonl.exists() else []
        seen = set(seen_file.read_text().split()) if seen_file.exists() else set()
        if accepted:
            report(len(accepted), intent.count, f"Resuming - {len(accepted)} already saved")

        def mark_seen(u):
            seen.add(u.lower())
            with open(seen_file, "a") as f:
                f.write(u.lower() + "\n")

        context: dict = {}
        stream = iter_candidates(holder.get, intent, seen, context)
        queue: deque = deque()
        failed_batch: list = []
        cooldowns = since_restart = 0

        while len(accepted) < intent.count:
            username = queue.popleft() if queue else next(stream, None)
            if username is None:
                stats["stopped"] = "ran out of candidates (search engines gave no more profiles)"
                break
            if username.lower() in seen:
                continue

            report(len(accepted), intent.count, f"Checking @{username}")
            try:
                profile = scrape_profile(holder.get(), username)
            except Exception as e:
                print(f"[pipeline] scrape error for {username}: {e}")
                profile = None

            if profile is None:
                failed_batch.append(username)
                if len(failed_batch) >= 3:   # 3 in a row = we're being blocked
                    cooldowns += 1
                    if cooldowns > max_cooldowns:
                        stats["stopped"] = "Instagram keeps blocking - try again later or use a proxy"
                        break
                    report(len(accepted), intent.count,
                           f"Blocked - cooling down {cooldown_seconds}s (#{cooldowns}/{max_cooldowns})")
                    queue.extendleft(reversed(failed_batch))   # retry them after the break
                    failed_batch.clear()
                    time.sleep(cooldown_seconds)
                    holder.restart()
                    since_restart = 0
                    continue
                time.sleep(random.uniform(*delay))
                continue

            # a success means earlier failures were individual (private/deleted), not a block
            for u in failed_batch:
                mark_seen(u)
                stats["unreadable"] += 1
            failed_batch.clear()
            mark_seen(username)
            stats["checked"] += 1
            since_restart += 1

            ctx = context.get(username.lower(), {})
            ok, why = passes_basic_filters(profile, intent)
            if ok and use_llm:
                profile = enrich_with_llm(profile, intent, ctx.get("text", ""))
                ok, why = passes_llm_filters(profile, intent)
            if ok and intent.country:
                verify = ((lambda u: verify_country_via_search(holder.get(), u, intent))
                          if verify_country else None)
                try:
                    verdict, evidence = resolve_country(profile, intent, ctx, verify)
                except Exception as e:
                    print(f"[pipeline] country check failed for {username}: {e}")
                    verdict, evidence = "unknown", ""
                profile.update(country_match=verdict, country_evidence=evidence,
                               country=intent.country if verdict in ("yes", "likely") else None)
                ok, why = passes_country_filter(profile, intent)
            if ok and not intent.country:
                # no country filter: still fill the country column (web lookup only if find_country)
                try:
                    c, ev = locate_country(profile, ctx.get("text", ""),
                                           holder.get() if find_country else None,
                                           use_web=find_country, use_llm=use_llm)
                except Exception as e:
                    print(f"[pipeline] country lookup failed for {username}: {e}")
                    c, ev = None, ""
                profile["country"], profile["country_evidence"] = c, ev
            if ok:
                profile.pop("raw_description", None)
                profile.pop("_llm_country", None)
                accepted.append(profile)
                with open(jsonl, "a", encoding="utf-8") as f:
                    f.write(json.dumps(profile, ensure_ascii=False) + "\n")
                _write_csv(csv_path, accepted)       # always keep the CSV current
            else:
                stats["rejected"] += 1

            if since_restart >= restart_every:
                holder.restart()
                since_restart = 0
            time.sleep(random.uniform(*delay))

        if len(accepted) < intent.count and stats["stopped"] == "target reached":
            stats["stopped"] = "stopped early"
        _write_csv(csv_path, accepted)
        stats["csv"] = str(csv_path)
        report(len(accepted), intent.count, f"Finished: {len(accepted)} profiles ({stats['stopped']})")
        return accepted, stats
    finally:
        holder.close()