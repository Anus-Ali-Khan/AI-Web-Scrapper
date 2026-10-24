"""Stage 1: find Instagram usernames via web search (Bing + DuckDuckGo), lazily, together with the
search-result snippet each profile appeared with (the snippet often reveals the location)."""
import base64
import random
import re
import time
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

from bs4 import BeautifulSoup

from country import mentions_country
from intent import SearchIntent

RESERVED = {
    "p", "reel", "reels", "explore", "tags", "stories", "accounts", "tv",
    "directory", "about", "legal", "web", "developer", "challenge", "direct",
    "locations", "popular", "topics", "privacy", "terms", "press", "api",
}
SUFFIXES = ["", "creator", "influencer", "content creator", "youtuber", "reviewer",
            "blogger", "founder", "educator", "tutorials", "tips", "podcast"]
RELATED_SUFFIXES = ["creator", "influencer", "reviews"]
GENDER_WORD = {"female": "woman", "male": "man"}
CAPTCHA_HINTS = ("captcha", "anomaly", "unusual traffic", "verify you are a human", "are you a robot")


def _real_url(href: str) -> str:
    """Bing wraps organic links in bing.com/ck/a?...&u=a1<base64>; unwrap those."""
    href = unquote(href)
    if "bing.com/ck/a" in href:
        u = parse_qs(urlparse(href).query).get("u", [""])[0]
        if u.startswith("a1"):
            b = u[2:] + "=" * (-len(u[2:]) % 4)
            try:
                return base64.urlsafe_b64decode(b).decode()
            except Exception:
                pass
    return href


def username_from_url(url: str) -> str | None:
    url = unquote(url)
    m = re.search(r"https?://(?:www\.)?instagram\.com/([^?#&\s\"']*)", url)
    if not m:
        return None
    parts = [p for p in m.group(1).split("/") if p]
    if not parts:
        return None
    name = parts[0]
    if name.lower() in RESERVED or not re.fullmatch(r"[A-Za-z0-9._]{1,30}", name):
        return None
    if len(parts) > 1 and parts[1] not in {"p", "reel", "reels", "tagged"}:
        return None
    return name


def extract_results(html: str) -> list[tuple[str, str]]:
    """[(username, snippet_text)] - snippet is the whole search-result block (title + description)."""
    soup = BeautifulSoup(html, "html.parser")
    out, names = [], set()
    for block in soup.select("li.b_algo, div.result, div.web-result"):
        text = block.get_text(" ", strip=True)
        for a in block.find_all("a", href=True):
            u = username_from_url(_real_url(a["href"]))
            if u:
                if u.lower() not in names:
                    names.add(u.lower())
                    out.append((u, text))
                break
    # stragglers outside recognised result blocks (no snippet available)
    for a in soup.find_all("a", href=True):
        u = username_from_url(_real_url(a["href"]))
        if u and u.lower() not in names:
            names.add(u.lower())
            out.append((u, ""))
    for m in re.finditer(r"\(@([A-Za-z0-9._]{1,30})\)", soup.get_text(" ")):
        if m.group(1).lower() not in names:
            names.add(m.group(1).lower())
            out.append((m.group(1), ""))
    return out


def extract_usernames(html: str) -> list[str]:
    return [u for u, _ in extract_results(html)]


def build_queries(intent: SearchIntent) -> list[str]:
    # The country goes in quotes so the engine must find it on the page (bio/snippet/location).
    where = f'"{intent.country}"' if intent.country else ""
    gword = GENDER_WORD.get(intent.gender or "", "")
    queries: list[str] = []

    def add(topic: str, suffixes: list[str]):
        for suf in suffixes:
            queries.append(f"site:instagram.com {topic} {where} {suf}".strip())
            if gword:
                queries.append(f"site:instagram.com {topic} {where} {gword} {suf}".strip())

    for tag in intent.hashtags:
        queries.append(f'site:instagram.com "#{tag}" {where}'.strip())
        queries.append(f"site:instagram.com #{tag} {intent.niche} {where}".strip())
    if intent.niche:
        add(intent.niche, SUFFIXES)
        # city / demonym queries pull in many extra local profiles ("tech creator" "Karachi")
        if intent.country:
            for alias in intent.country_aliases[:14]:
                if len(alias) > 3 and alias.isascii() and alias.lower() != intent.country.lower():
                    queries.append(f'site:instagram.com {intent.niche} creator "{alias}"')
    for topic in intent.related_topics:
        add(topic, RELATED_SUFFIXES)

    seen, out = set(), []
    for q in queries:
        q = re.sub(r"\s+", " ", q)
        if q not in seen:
            seen.add(q)
            out.append(q)
    return out


def _search_url(engine: str, query: str, page: int) -> str:
    q = quote_plus(query)
    if engine == "bing":
        return f"https://www.bing.com/search?q={q}&first={page * 10 + 1}"
    return f"https://html.duckduckgo.com/html/?q={q}"


def verify_country_via_search(browser, username: str, intent: SearchIntent) -> tuple[str | None, str]:
    """Extra check for profiles whose country is still unknown: ask the search engine for this
    exact profile + the country and see whether the result snippet mentions the country."""
    query = f'site:instagram.com/{username} "{intent.country}"'
    html = browser.get_html(_search_url("bing", query, 0), 2, 4)
    if any(h in html.lower() for h in CAPTCHA_HINTS):
        return None, ""
    for u, text in extract_results(html):
        if u.lower() == username.lower():
            hit = mentions_country(text, intent.country_aliases or [intent.country])
            if hit:
                return "yes", f"verification search ('{hit}')"
    return None, ""


def iter_candidates(get_browser, intent: SearchIntent, seen: set, context: dict, max_pages: int = 4):
    """Yields new usernames one at a time and fills `context[username.lower()]` with
    {'text': snippets, 'via_country_query': bool}. `seen` is shared with the pipeline."""
    dead_engines: set = set()
    yielded: set = set()

    for query in build_queries(intent):
        for engine in ("bing", "ddg"):
            if engine in dead_engines:
                continue
            for page in range(max_pages if engine == "bing" else 1):
                try:
                    html = get_browser().get_html(_search_url(engine, query, page), 2, 4)
                except Exception as e:
                    print(f"[discover] {engine} fetch failed: {e}")
                    break
                if any(h in html.lower() for h in CAPTCHA_HINTS):
                    print(f"[discover] {engine} is showing a captcha - skipping this engine.")
                    dead_engines.add(engine)
                    break
                new = []
                for u, text in extract_results(html):
                    key = u.lower()
                    ctx = context.setdefault(key, {"text": "", "via_country_query": False})
                    if text and text not in ctx["text"]:
                        ctx["text"] = (ctx["text"] + " " + text)[:2000]
                    if intent.country:
                        ctx["via_country_query"] = True
                    if key not in seen and key not in yielded:
                        new.append(u)
                if not new:
                    break  # nothing fresh on this page - deeper pages won't help
                for u in dict.fromkeys(new):
                    yielded.add(u.lower())
                    yield u
                time.sleep(random.uniform(1, 3))
        if len(dead_engines) == 2:
            print("[discover] Both search engines are blocking - discovery stopped.")
            return