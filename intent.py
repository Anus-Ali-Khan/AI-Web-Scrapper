"""Turn a free-text request into a structured SearchIntent.
The LLM does the parsing; a regex fallback fills gaps / covers LLM failures."""
import json
import re
from dataclasses import asdict, dataclass, field

import os
MODEL_NAME = os.environ.get("OLLAMA_MODEL", "llama3")   # must match a name shown by `ollama list`


@dataclass
class SearchIntent:
    mode: str = "discover"                 # "discover" many creators | "profile" one account
    profile_query: str | None = None       # handle or name (mode="profile")
    niche: str = ""                        # e.g. "tech"
    country: str | None = None
    gender: str | None = None              # "male" | "female"
    hashtags: list = field(default_factory=list)
    count: int = 20
    min_followers: int | None = None
    max_followers: int | None = None
    related_topics: list = field(default_factory=list)   # LLM-made sub-niches for wider search
    country_aliases: list = field(default_factory=list)  # country name + demonyms, cities, flag...
    strict: bool = False                   # True: drop profiles whose country/gender can't be verified

    def to_dict(self):
        return asdict(self)


STOPWORDS = set("""find search give get show me for list of the a an instagram creators creator
influencers influencer profiles profile accounts account people from in who are with and to please
top best male female men women man woman guys girls followers over under more less than at least
most based""".split())


def _num(value: str, suffix: str) -> int:
    n = float(value)
    return int(n * {"k": 1_000, "m": 1_000_000}.get(suffix.lower(), 1))


def _rule_based(prompt: str) -> dict:
    p = prompt.strip()
    out: dict = {}

    handle = re.search(r"@([A-Za-z0-9._]{2,30})", p)
    named = re.search(
        r"(?:find|search|get|show)\s+(?:this\s+|the\s+)?(?:instagram\s+)?(?:profile|account|user)\s+"
        r"(?:of\s+|named\s+|for\s+)?@?([\w.\- ]{2,40})", p, re.I)
    if handle:
        out["mode"], out["profile_query"] = "profile", handle.group(1)
    elif named:
        out["mode"], out["profile_query"] = "profile", named.group(1).strip()

    out["hashtags"] = [h.lower() for h in re.findall(r"#(\w+)", p)]

    m = re.search(r"\b(\d[\d,]{0,5})\s+(?:[\w-]+\s+){0,3}?(creators|influencers|profiles|accounts|people)\b", p, re.I)
    if m:
        out["count"] = int(m.group(1).replace(",", ""))

    if re.search(r"\b(female|women|woman|girls?|ladies)\b", p, re.I):
        out["gender"] = "female"
    elif re.search(r"\b(male|men|man|guys?|boys?)\b", p, re.I):
        out["gender"] = "male"

    m = re.search(r"(?:over|more than|above|at least|>)\s*(\d+(?:\.\d+)?)\s*([kKmM]?)\s*followers", p)
    if m:
        out["min_followers"] = _num(m.group(1), m.group(2))
    m = re.search(r"(?:under|less than|below|at most|<)\s*(\d+(?:\.\d+)?)\s*([kKmM]?)\s*followers", p)
    if m:
        out["max_followers"] = _num(m.group(1), m.group(2))

    m = re.search(r"(?:from|in|based in)\s+([A-Z][A-Za-z]+(?:\s[A-Z][A-Za-z]+)?)", p)
    if m:
        out["country"] = m.group(1)

    # niche = whatever is left after removing filler words / extracted bits
    rest = re.sub(r"#\w+|@\w+|\b\d[\d,.]*\s*[kKmM]?\b", " ", p)
    if out.get("country"):
        rest = rest.replace(out["country"], " ")
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z\-+]*", rest) if w.lower() not in STOPWORDS]
    out["niche"] = " ".join(words)
    return out


def _llm():
    from langchain_ollama import OllamaLLM
    return OllamaLLM(model=MODEL_NAME)


PARSE_PROMPT = """Convert this Instagram search request into JSON.

Keys:
- mode: "profile" if the user wants ONE specific account (a name or @handle), otherwise "discover"
- profile_query: the handle or name (profile mode only), else null
- niche: topic of the creators, e.g. "tech", "AI tools", "fitness" ("" in profile mode)
- country: country name or null
- gender: "male", "female" or null
- hashtags: list of hashtags without the # sign
- count: how many creators the user wants (integer; 20 if not stated)
- min_followers, max_followers: integers or null (expand 10k -> 10000, 1M -> 1000000)

Return ONLY the JSON object.

Request: {prompt}"""


def _related_topics(niche: str) -> list:
    from langchain_core.prompts import ChatPromptTemplate
    chain = ChatPromptTemplate.from_template(
        "List 15 specific sub-topics or neighbouring niches of \"{niche}\" that Instagram creators "
        "post about. Return ONLY a JSON array of short strings.") | _llm()
    try:
        raw = chain.invoke({"niche": niche})
        m = re.search(r"\[.*\]", raw, re.DOTALL)
        return [str(t).strip() for t in json.loads(m.group(0)) if str(t).strip()][:15]
    except Exception as e:
        print(f"[intent] related-topics step failed: {e}")
        return []


def _as_int(v):
    try:
        return int(float(str(v).replace(",", ""))) if v not in (None, "", "null") else None
    except ValueError:
        return None


def parse_prompt(prompt: str, use_llm: bool = True) -> SearchIntent:
    rb = _rule_based(prompt)
    data: dict = {}
    if use_llm:
        try:
            from langchain_core.prompts import ChatPromptTemplate
            raw = (ChatPromptTemplate.from_template(PARSE_PROMPT) | _llm()).invoke({"prompt": prompt})
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            data = json.loads(m.group(0)) if m else {}
        except Exception as e:
            print(f"[intent] LLM parse failed, using rules only: {e}")

    def pick(key, default=None):
        v = data.get(key)
        return v if v not in (None, "", [], "null") else rb.get(key, default)

    gender = str(pick("gender") or "").lower()
    intent = SearchIntent(
        mode="profile" if pick("mode", "discover") == "profile" and pick("profile_query") else "discover",
        profile_query=(str(pick("profile_query")).lstrip("@").strip() if pick("profile_query") else None),
        niche=str(pick("niche", "") or "").strip(),
        country=(str(pick("country")).strip() if pick("country") else None),
        gender=gender if gender in ("male", "female") else None,
        hashtags=[str(h).lstrip("#").lower() for h in (pick("hashtags", []) or [])],
        count=max(1, min(_as_int(pick("count", 20)) or 20, 2000)),
        min_followers=_as_int(pick("min_followers")),
        max_followers=_as_int(pick("max_followers")),
    )
    if intent.mode == "discover" and intent.country:
        from country import expand_aliases
        intent.country_aliases = expand_aliases(intent.country, use_llm)
    if use_llm and intent.mode == "discover" and intent.niche and intent.count > 25:
        intent.related_topics = _related_topics(intent.niche)
    return intent