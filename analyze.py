"""LLM enrichment + filtering. Gender/country are ESTIMATES from text, not facts."""
import json
import re

from intent import MODEL_NAME, SearchIntent

MIN_RELEVANCE = 5

PROMPT = """You are screening a public Instagram profile.

Search goal: creators about "{niche}"
Country wanted: {country}  (related words: {aliases})
Hashtags of interest: {hashtags}

Profile text:
{text}

Text of the search-result snippet this profile was found with (may mention a location):
{snippet}

Return ONLY a JSON object with these keys:
- "niche": short label of the topic they post about
- "summary": one sentence on what they post
- "email": an email ONLY if it literally appears in the text, else null
- "relevance": integer 1-10, how well they match the search goal
- "account_type": "individual", "brand_or_organization" or "unknown"
- "gender": "male", "female" or "unknown". Base it ONLY on explicit signals (pronouns, words like
  "mom"/"father", an unambiguous first name). Brands, groups and unclear cases are "unknown".
- "location": city/country ONLY if stated in the text (words or flag emoji), else null
- "country_match": "yes" if the text ties them to the wanted country, "no" ONLY if the text clearly
  places them in a different country, otherwise "unknown"
Do not guess or invent anything not supported by the text."""

_chain = None


def _get_chain():
    global _chain
    if _chain is None:
        from langchain_core.prompts import ChatPromptTemplate
        from langchain_ollama import OllamaLLM
        _chain = ChatPromptTemplate.from_template(PROMPT) | OllamaLLM(model=MODEL_NAME)
    return _chain


def _one_of(value, allowed, default="unknown"):
    v = str(value).strip().lower()
    return v if v in allowed else default


def enrich_with_llm(profile: dict, intent: SearchIntent, snippet: str = "") -> dict:
    text = (
        f"Name: {profile.get('full_name')}\nUsername: {profile['username']}\n"
        f"Bio: {profile.get('bio')}\nMeta description: {profile.get('raw_description')}"
    )
    data: dict = {}
    err = ""
    try:
        raw = _get_chain().invoke({
            "niche": intent.niche or "any", "country": intent.country or "none",
            "aliases": ", ".join(intent.country_aliases[:12]) or "none",
            "hashtags": ", ".join(intent.hashtags) or "none",
            "text": text, "snippet": snippet[:800] or "none",
        })
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        data = json.loads(m.group(0)) if m else {}
    except Exception as e:
        err = str(e)
        print(f"[analyze] LLM step failed for {profile['username']}: {e}")

    try:
        relevance = int(data.get("relevance"))
    except (TypeError, ValueError):
        relevance = None

    profile.update({
        "niche": data.get("niche"),
        "summary": data.get("summary"),
        "email": data.get("email"),
        "relevance": relevance,
        "account_type": _one_of(data.get("account_type"), {"individual", "brand_or_organization"}),
        "gender": _one_of(data.get("gender"), {"male", "female"}),
        "location": data.get("location"),
        "llm_status": "ok" if data else f"failed: {err or 'reply had no JSON'}"[:120],
        "_llm_country": _one_of(data.get("country_match"), {"yes", "no"}),  # internal; see country.py
    })
    if intent.gender:
        g = profile["gender"]
        profile["gender_match"] = "unknown" if g == "unknown" else ("yes" if g == intent.gender else "no")
    else:
        profile["gender_match"] = "n/a"
    if not intent.country:
        profile["country_match"] = "n/a"
    return profile


def passes_basic_filters(profile: dict, intent: SearchIntent) -> tuple[bool, str]:
    """Cheap checks that need no LLM (run first to save time)."""
    f = profile.get("followers")
    if intent.min_followers and (f is None or f < intent.min_followers):
        return False, "below min followers"
    if intent.max_followers and (f is None or f > intent.max_followers):
        return False, "above max followers"
    return True, ""


def passes_llm_filters(profile: dict, intent: SearchIntent) -> tuple[bool, str]:
    rel = profile.get("relevance")
    if rel is not None and rel < MIN_RELEVANCE:
        return False, f"low relevance ({rel})"
    gm = profile.get("gender_match")
    if gm == "no" or (gm == "unknown" and intent.strict):
        return False, "gender mismatch/unverified"
    return True, ""


def passes_country_filter(profile: dict, intent: SearchIntent) -> tuple[bool, str]:
    """no -> always dropped. unknown/likely -> kept, unless strict (strict keeps only verified 'yes')."""
    cm = profile.get("country_match")
    if cm == "no":
        return False, "country mismatch"
    if cm in ("unknown", "likely") and intent.strict:
        return False, "country not verified"
    return True, ""