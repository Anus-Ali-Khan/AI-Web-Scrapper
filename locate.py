"""Find which country a creator is in, even when Instagram doesn't say and nobody asked for a
specific country. Order: profile text -> search snippet -> web search about the person."""
import json
import re

from bs4 import BeautifulSoup

from country import (detect_all_countries, detect_any_country, detect_domain_country,
                     detect_phone_country)
from discover import CAPTCHA_HINTS, _real_url, _search_url

PROMPT = """Web search snippets about the Instagram creator "{name}" (@{username}):
{snippets}

Which country is THIS person based in or from? Only answer if a snippet clearly says so about this
same person (not a namesake). Return ONLY JSON:
{{"country": "<country name or null>", "evidence": "<short phrase copied EXACTLY from the snippets>"}}"""


INFLUENCER_SITES = ("hypeauditor.com", "heepsy.com", "starngage.com")
# "Country rank: 709 in Switzerland Based on overall account performance..."   (profile pages)
RANK_RE = re.compile(r"Country rank:?\s*[\d,.]+\s+in\s+((?:[A-Z][a-z]+)(?:\s(?:of\s)?[A-Z][a-z]+){0,2})"
                     r"(?=\s+Based|\s+Category|\s*$|\s*[.,;])")
# "Top Instagram influencers in New Zealand for June 2026"                      (ranking pages)
TITLE_RE = re.compile(r"[Ii]nfluencers\s+in\s+((?:[A-Z][a-z]+)(?:\s[A-Z][a-z]+){0,2})"
                      r"(?=\s+for\b|\s*[-\u2013|:]|\s*$)")


def _result_blocks(browser, query: str) -> list[tuple[str, str]]:
    try:
        html = browser.get_html(_search_url("bing", query, 0), 2, 4)
    except Exception as e:
        print(f"[locate] search failed: {e}")
        return []
    if any(h in html.lower() for h in CAPTCHA_HINTS):
        return []
    out = []
    for b in BeautifulSoup(html, "html.parser").select("li.b_algo"):
        a = b.find("a", href=True)
        if a:
            out.append((_real_url(a["href"]), b.get_text(" ", strip=True)))
    return out


def lookup_influencer_sites(browser, username: str):
    """Influencer-analytics sites (HypeAuditor, Heepsy...) already guess each creator's country.
    Reads the search snippet; if it has no country, opens the matching result page once."""
    u = username.lower()
    sites = " OR ".join(f"site:{d}" for d in INFLUENCER_SITES)
    for href, text in _result_blocks(browser, f'"{username}" ({sites})'):
        domain = next((d for d in INFLUENCER_SITES if d in href.lower()), None)
        if not domain or (u not in href.lower() and u not in text.lower()):
            continue                      # must be about THIS username
        m = RANK_RE.search(text) or (TITLE_RE.search(text) if u in text.lower() else None)
        if m:
            return m.group(1), f"influencer site ({domain})"
        if u in href.lower():             # profile page of this user: open it and read the country rank
            try:
                page = BeautifulSoup(browser.get_html(href, 3, 5), "html.parser").get_text(" ", strip=True)
                m = RANK_RE.search(page)
                if m:
                    return m.group(1), f"influencer site ({domain})"
            except Exception as e:
                print(f"[locate] could not open {domain}: {e}")
            break
    return None, ""


def _snippets(browser, query: str, limit: int = 6) -> list[str]:
    try:
        html = browser.get_html(_search_url("bing", query, 0), 2, 4)
    except Exception as e:
        print(f"[locate] search failed: {e}")
        return []
    if any(h in html.lower() for h in CAPTCHA_HINTS):
        return []
    soup = BeautifulSoup(html, "html.parser")
    return [b.get_text(" ", strip=True)[:500] for b in soup.select("li.b_algo")][:limit]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower()).strip()


def _ask_llm(name: str, username: str, snippets: str):
    try:
        from langchain_core.prompts import ChatPromptTemplate
        from intent import _llm
        raw = (ChatPromptTemplate.from_template(PROMPT) | _llm()).invoke(
            {"name": name or username, "username": username, "snippets": snippets})
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        data = json.loads(m.group(0)) if m else {}
    except Exception as e:
        print(f"[locate] LLM step failed: {e}")
        return None, ""
    country, evidence = data.get("country"), str(data.get("evidence") or "")
    # guardrail: the quoted evidence must really be in the snippets, otherwise ignore the answer
    if country and str(country).lower() != "null" and evidence and _norm(evidence) in _norm(snippets):
        return str(country).strip(), evidence[:80]
    return None, ""


def locate_country(profile: dict, snippet: str = "", browser=None, use_web: bool = True,
                   use_llm: bool = True) -> tuple[str | None, str]:
    """Returns (country or None, evidence)."""
    name, username = (profile.get("full_name") or "").strip(), profile["username"]

    own = " ".join(str(profile.get(k) or "") for k in ("full_name", "bio", "raw_description", "location"))

    # --- free checks (no extra requests) ---
    hit = detect_any_country(own)                                     # 1. place / flag in the bio
    if hit:
        return hit[0], f"profile text ('{hit[1]}')"
    hit = detect_phone_country(own + " " + str(profile.get("external_url") or ""))   # 2. +92 / wa.me numbers
    if hit:
        return hit[0], f"phone code ('{hit[1]}')"
    hit = detect_domain_country(profile.get("external_url", ""), profile.get("bio", ""))
    if hit:                                                           # 3. .pk / .in ... bio link
        return hit[0], f"bio link domain ('{hit[1]}')"
    hit = detect_any_country(snippet)                                 # 4. search-result snippet
    if hit:
        return hit[0], f"search snippet ('{hit[1]}')"
    if not (use_web and browser):
        return None, ""

    # --- web checks (extra searches) ---
    country, evidence = lookup_influencer_sites(browser, username)    # 5. HypeAuditor / Heepsy...
    if country:
        return country, evidence

    label = name or username
    snips: list[str] = []
    for q in (f'"{label}" "{username}"', f'"{label}" creator based in OR from OR born in'):
        snips += _snippets(browser, q)
    needles = [n.lower() for n in (name, username) if len(n) > 2]
    relevant = [s for s in dict.fromkeys(snips) if any(n in s.lower() for n in needles)]
    if not relevant:
        return None, ""

    if use_llm:
        country, evidence = _ask_llm(name, username, "\n".join(f"- {s}" for s in relevant[:8]))
        if country:
            return country, f"web search ('{evidence}')"
    found = detect_all_countries(" ".join(relevant))
    if len(found) == 1:                      # only trust the rule-based path if it is unambiguous
        country, alias = next(iter(found.items()))
        return country, f"web search ('{alias}')"
    return None, ""