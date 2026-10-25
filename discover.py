"""Stage 1: find Instagram usernames via web search (Bing + DuckDuckGo), lazily, together with the
search-result snippet each profile appeared with (the snippet often reveals the location)."""
import base64
import os
import random
import re
import time
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

from bs4 import BeautifulSoup

from country import SYNONYMS, mentions_country
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


US_LOCATIONS = [
    "New York", "Los Angeles", "Chicago", "Houston", "Phoenix", "Philadelphia", "San Antonio", "San Diego",
    "Dallas", "San Jose", "Austin", "Jacksonville", "Fort Worth", "Columbus", "Charlotte", "San Francisco",
    "Indianapolis", "Seattle", "Denver", "Washington DC", "Boston", "Nashville", "Detroit", "Portland",
    "Las Vegas", "Memphis", "Louisville", "Baltimore", "Milwaukee", "Albuquerque", "Tucson", "Sacramento",
    "Kansas City", "Atlanta", "Miami", "Raleigh", "Omaha", "Minneapolis", "Tampa", "Orlando", "Pittsburgh",
    "Cincinnati", "Cleveland", "St. Louis", "Salt Lake City", "Honolulu", "New Orleans", "Brooklyn",
    "Silicon Valley", "Palo Alto", "Irvine", "Boulder", "San Mateo", "Oakland", "Scottsdale", "Plano",
    "California", "Texas", "Florida", "New York State", "Washington State", "Illinois", "Georgia", "Colorado",
    "Massachusetts", "Virginia", "North Carolina", "Arizona", "Ohio", "Michigan", "Oregon", "Utah",
]
GL_CODES = {"united states": "us", "pakistan": "pk", "india": "in", "united kingdom": "gb", "canada": "ca",
            "australia": "au", "uae": "ae", "saudi arabia": "sa", "germany": "de", "france": "fr"}


def locations_for(intent: SearchIntent) -> list[str]:
    """Cities/regions to combine with each topic. Bios say '📍Austin, TX', not 'United States',
    so city queries find far more profiles than the country name does."""
    if not intent.country:
        return []
    key = SYNONYMS.get(intent.country.strip().lower(), intent.country.strip().lower())
    if key == "united states":
        return US_LOCATIONS
    return [a for a in intent.country_aliases if len(a) > 3 and a.isascii()
            and a.lower() != intent.country.lower()]


def build_queries(intent: SearchIntent) -> list[str]:
    where = intent.country or ""          # NOT in quotes: indexed Instagram text rarely contains it
    gword = GENDER_WORD.get(intent.gender or "", "")
    queries: list[str] = []

    for tag in intent.hashtags:
        queries.append(f'site:instagram.com "#{tag}" {where}'.strip())
        queries.append(f"site:instagram.com #{tag} {intent.niche} {where}".strip())

    # 1) the niche with generic suffixes
    if intent.niche:
        for suf in SUFFIXES:
            queries.append(f"site:instagram.com {intent.niche} {where} {gword} {suf}")

    # 2) topic x city matrix - this is what gives volume (core niche x every city first)
    topics = ([intent.niche] if intent.niche else []) + intent.related_topics[:10]
    for topic in topics:
        for loc in locations_for(intent):
            for suf in ("creator", "influencer"):
                queries.append(f"site:instagram.com {topic} {loc} {gword} {suf}")

    # 3) related topics with generic suffixes
    for topic in intent.related_topics:
        for suf in RELATED_SUFFIXES:
            queries.append(f"site:instagram.com {topic} {where} {gword} {suf}")

    seen, out = set(), []
    for q in queries:
        q = re.sub(r"\s+", " ", q).strip()
        if q not in seen:
            seen.add(q)
            out.append(q)
    return out


def _search_url(engine: str, query: str, page: int) -> str:
    q = quote_plus(query)
    if engine == "bing":
        return f"https://www.bing.com/search?q={q}&first={page * 10 + 1}"
    return f"https://html.duckduckgo.com/html/?q={q}"


API_CALLS = 0            # how many Serper requests this run has used (1 per result page)
API_CALL_LIMIT = None    # set by run.py --max-api-calls to cap spending


def _serper_page(query: str, page: int, gl: str | None = None) -> list[tuple[str, str]]:
    """One page of Google results through the Serper.dev API -> [(username, title + snippet)].
    Needs the SERPER_API_KEY environment variable. `page` is 1-based."""
    import requests
    global API_CALLS
    if API_CALL_LIMIT is not None and API_CALLS >= API_CALL_LIMIT:
        raise RuntimeError(f"API call limit reached ({API_CALL_LIMIT})")
    API_CALLS += 1
    body = {"q": query, "num": 10, "page": page}
    if gl:
        body["gl"] = gl
    r = requests.post("https://google.serper.dev/search", timeout=30,
                      headers={"X-API-KEY": os.environ["SERPER_API_KEY"], "Content-Type": "application/json"},
                      json=body)
    r.raise_for_status()
    out = []
    for item in r.json().get("organic", []):
        u = username_from_url(item.get("link", ""))
        if u:
            out.append((u, f"{item.get('title', '')} {item.get('snippet', '')}".strip()))
    return out


def verify_country_via_search(browser, username: str, intent: SearchIntent) -> tuple[str | None, str]:
    """Extra check for profiles whose country is still unknown: search this exact profile + the
    country and see whether the result snippet mentions the country."""
    query = f'site:instagram.com/{username} "{intent.country}"'
    if os.environ.get("SERPER_API_KEY"):
        results = _serper_page(query, 1)
    else:
        html = browser.get_html(_search_url("bing", query, 0), 2, 4)
        if any(h in html.lower() for h in CAPTCHA_HINTS):
            return None, ""
        results = extract_results(html)
    for u, text in results:
        if u.lower() == username.lower():
            hit = mentions_country(text, intent.country_aliases or [intent.country])
            if hit:
                return "yes", f"verification search ('{hit}')"
    return None, ""


def _fetch(engine: str, query: str, page: int, get_browser, gl: str | None):
    """Returns [(username, snippet)] or None if the engine is showing a captcha."""
    if engine == "serper":
        return _serper_page(query, page + 1, gl)
    html = get_browser().get_html(_search_url(engine, query, page), 2, 4)
    if any(h in html.lower() for h in CAPTCHA_HINTS):
        return None
    return extract_results(html)


def iter_candidates(get_browser, intent: SearchIntent, seen: set, context: dict, max_pages: int | None = None):
    """Yields new usernames one at a time and fills `context[username.lower()]` with
    {'text': snippets, 'via_country_query': bool}. `seen` is shared with the pipeline.
    Backend: Google through Serper if SERPER_API_KEY is set, otherwise Bing/DuckDuckGo in Chrome."""
    use_api = bool(os.environ.get("SERPER_API_KEY"))
    engines = ["serper"] if use_api else ["bing", "ddg"]
    max_pages = max_pages or (5 if use_api else 4)
    gl = GL_CODES.get(SYNONYMS.get((intent.country or "").lower(), (intent.country or "").lower()))
    queries = build_queries(intent)
    print(f"[discover] {len(queries)} queries planned - backend: "
          f"{'Google via Serper API' if use_api else 'Bing/DuckDuckGo in Chrome'}")

    dead: dict = {}
    yielded: set = set()
    found_total = pages_total = 0

    for qi, query in enumerate(queries, start=1):
        for engine in engines:
            if engine in dead:
                continue
            for page in range(1 if engine == "ddg" else max_pages):
                try:
                    results = _fetch(engine, query, page, get_browser, gl)
                except Exception as e:
                    print(f"[discover] {engine} error: {e}")
                    dead[engine] = f"error: {e}"
                    break
                if results is None:
                    dead[engine] = "captcha / blocked"
                    print(f"[discover] {engine} is showing a captcha - switching it off.")
                    break
                pages_total += 1
                new = []
                for u, text in results:
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
                    found_total += 1
                    yield u
                time.sleep(0.3 if use_api else random.uniform(1, 3))
        if qi % 25 == 0:
            print(f"[discover] query {qi}/{len(queries)} - {found_total} new profiles so far")
        if len(dead) == len(engines):
            print(f"[discover] STOPPED: every search backend is blocked/failing: {dead}")
            return
    print(f"[discover] used all {len(queries)} queries ({pages_total} result pages) - "
          f"{found_total} profiles found. Engines: {dead or 'all fine'}")