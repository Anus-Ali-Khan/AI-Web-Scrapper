"""Find which country a creator is in, even when Instagram doesn't say and nobody asked for a
specific country. Order: profile text -> search snippet -> web search about the person."""
import json
import re

from bs4 import BeautifulSoup

from country import detect_all_countries, detect_any_country
from discover import CAPTCHA_HINTS, _search_url

PROMPT = """Web search snippets about the Instagram creator "{name}" (@{username}):
{snippets}

Which country is THIS person based in or from? Only answer if a snippet clearly says so about this
same person (not a namesake). Return ONLY JSON:
{{"country": "<country name or null>", "evidence": "<short phrase copied EXACTLY from the snippets>"}}"""


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
    hit = detect_any_country(own)
    if hit:
        return hit[0], f"profile text ('{hit[1]}')"
    hit = detect_any_country(snippet)
    if hit:
        return hit[0], f"search snippet ('{hit[1]}')"
    if not (use_web and browser):
        return None, ""

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