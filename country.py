"""Country detection. Instagram's public page rarely states a country, so we collect evidence from
(1) the bio, (2) the search-result snippet the profile was found with, and (3) an optional
per-profile verification search - all matched against the country name + aliases
(demonyms, big cities, abbreviations, flag emoji)."""
import json
import re

# Fallback aliases so country search works even with --no-llm. The LLM adds more.
SYNONYMS = {"usa": "united states", "us": "united states", "america": "united states",
            "united states of america": "united states", "uk": "united kingdom", "britain": "united kingdom",
            "england": "united kingdom", "great britain": "united kingdom",
            "united arab emirates": "uae", "emirates": "uae", "ksa": "saudi arabia"}
BUILTIN = {
    "pakistan": ["Pakistani", "Karachi", "Lahore", "Islamabad", "Rawalpindi", "Faisalabad", "Peshawar", "Multan", "PK", "🇵🇰"],
    "india": ["Indian", "Mumbai", "Delhi", "New Delhi", "Bangalore", "Bengaluru", "Hyderabad", "Chennai", "Kolkata", "Pune", "🇮🇳"],
    "united states": ["USA", "American", "New York", "Los Angeles", "San Francisco", "Chicago", "Texas", "California", "Seattle", "🇺🇸"],
    "united kingdom": ["UK", "British", "London", "Manchester", "Birmingham", "Scotland", "🇬🇧"],
    "canada": ["Canadian", "Toronto", "Vancouver", "Montreal", "Ottawa", "Calgary", "🇨🇦"],
    "australia": ["Australian", "Sydney", "Melbourne", "Brisbane", "Perth", "🇦🇺"],
    "uae": ["United Arab Emirates", "Emirati", "Dubai", "Abu Dhabi", "Sharjah", "🇦🇪"],
    "saudi arabia": ["Saudi", "Riyadh", "Jeddah", "KSA", "🇸🇦"],
    "bangladesh": ["Bangladeshi", "Dhaka", "Chittagong", "🇧🇩"],
    "nigeria": ["Nigerian", "Lagos", "Abuja", "🇳🇬"],
    "germany": ["German", "Berlin", "Munich", "Hamburg", "🇩🇪"],
    "turkey": ["Turkish", "Istanbul", "Ankara", "🇹🇷"],
}


def expand_aliases(country: str, use_llm: bool = True) -> list:
    key = country.strip().lower()
    key = SYNONYMS.get(key, key)
    aliases = [country.strip()] + BUILTIN.get(key, [])
    if use_llm:
        try:
            from langchain_core.prompts import ChatPromptTemplate
            from intent import _llm
            chain = ChatPromptTemplate.from_template(
                "List up to 15 words a social-media bio might use to show someone is from or based in "
                "{country}: demonyms, major cities, common abbreviations, and the flag emoji. "
                "Return ONLY a JSON array of strings.") | _llm()
            raw = chain.invoke({"country": country})
            m = re.search(r"\[.*\]", raw, re.DOTALL)
            aliases += [str(a).strip() for a in json.loads(m.group(0)) if str(a).strip()]
        except Exception as e:
            print(f"[country] alias LLM step failed, using built-in list: {e}")
    seen, out = set(), []
    for a in aliases:
        if a.lower() not in seen:
            seen.add(a.lower())
            out.append(a)
    return out


def mentions_country(text: str, aliases: list) -> str | None:
    """Returns the alias found in text, or None. Short codes (PK, UK, UAE) must be UPPERCASE so that
    ordinary words like 'us' or 'uk' in lowercase don't count."""
    if not text:
        return None
    for a in aliases:
        flags = 0 if (len(a) <= 3 and a.isalpha()) else re.IGNORECASE
        if re.search(rf"(?<![A-Za-z]){re.escape(a)}(?![A-Za-z])", text, flags):
            return a
    return None


def resolve_country(profile: dict, intent, ctx: dict, verify=None) -> tuple[str, str]:
    """Returns (verdict, evidence). verdict: yes | likely | no | unknown.
    yes = country/alias literally found in text; likely = only matched via country-restricted search."""
    aliases = intent.country_aliases or [intent.country]
    own_text = " ".join(str(profile.get(k) or "") for k in ("full_name", "bio", "raw_description", "location"))
    hit = mentions_country(own_text, aliases)
    if hit:
        return "yes", f"profile text ('{hit}')"
    hit = mentions_country(ctx.get("text", ""), aliases)
    if hit:
        return "yes", f"search snippet ('{hit}')"
    if profile.get("_llm_country") == "no":
        return "no", "profile text points to another country"
    if verify:
        verdict, evidence = verify(profile["username"])
        if verdict == "yes":
            return "yes", evidence
    if ctx.get("via_country_query"):
        return "likely", "found via country-restricted search (not stated on profile)"
    return "unknown", ""


# ---------------------------------------------------------------------------------------------
# Detecting a country when the user did NOT ask for a specific one (e.g. "find this profile X")
# ---------------------------------------------------------------------------------------------
BUILTIN.update({
    "egypt": ["Egyptian", "Cairo", "Alexandria", "Giza", "🇪🇬"],
    "indonesia": ["Indonesian", "Jakarta", "Bali", "Surabaya", "Bandung", "🇮🇩"],
    "malaysia": ["Malaysian", "Kuala Lumpur", "Penang", "Johor", "🇲🇾"],
    "philippines": ["Filipino", "Manila", "Cebu", "Davao", "🇵🇭"],
    "brazil": ["Brazilian", "São Paulo", "Sao Paulo", "Rio de Janeiro", "Brasil", "🇧🇷"],
    "mexico": ["Mexican", "Mexico City", "Guadalajara", "Monterrey", "🇲🇽"],
    "france": ["French", "Paris", "Lyon", "Marseille", "🇫🇷"],
    "spain": ["Spanish", "Madrid", "Barcelona", "Valencia", "🇪🇸"],
    "italy": ["Italian", "Rome", "Milan", "Naples", "🇮🇹"],
    "netherlands": ["Dutch", "Amsterdam", "Rotterdam", "🇳🇱"],
    "south africa": ["South African", "Cape Town", "Johannesburg", "Durban", "🇿🇦"],
    "kenya": ["Kenyan", "Nairobi", "Mombasa", "🇰🇪"],
    "sri lanka": ["Sri Lankan", "Colombo", "Kandy", "🇱🇰"],
    "nepal": ["Nepali", "Kathmandu", "Pokhara", "🇳🇵"],
    "singapore": ["Singaporean", "🇸🇬"],
    "japan": ["Japanese", "Tokyo", "Osaka", "Kyoto", "🇯🇵"],
    "south korea": ["Korean", "Seoul", "Busan", "🇰🇷"],
    "china": ["Chinese", "Beijing", "Shanghai", "Shenzhen", "🇨🇳"],
    "russia": ["Russian", "Moscow", "Saint Petersburg", "🇷🇺"],
    "iran": ["Iranian", "Tehran", "🇮🇷"],
    "qatar": ["Qatari", "Doha", "🇶🇦"],
    "kuwait": ["Kuwaiti", "Kuwait City", "🇰🇼"],
    "jordan": ["Jordanian", "Amman", "🇯🇴"],
    "morocco": ["Moroccan", "Casablanca", "Marrakech", "🇲🇦"],
    "argentina": ["Argentinian", "Buenos Aires", "🇦🇷"],
})
DISPLAY = {"uae": "United Arab Emirates"}


def display_name(key: str) -> str:
    return DISPLAY.get(key, key.title())


def _earliest(text: str, aliases: list):
    """(position, alias) of the earliest alias occurring in text, or None."""
    best = None
    for a in aliases:
        flags = 0 if (len(a) <= 3 and a.isalpha()) else re.IGNORECASE
        m = re.search(rf"(?<![A-Za-z]){re.escape(a)}(?![A-Za-z])", text, flags)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), a)
    return best


def detect_any_country(text: str):
    """(country, matched_alias) for the best country mentioned in text, else None.
    Strong clues (country name, flag emoji, short code like PK/UK) beat weak ones (cities, demonyms)."""
    if not text:
        return None
    for strong_only in (True, False):
        best = None
        for key, aliases in BUILTIN.items():
            name = display_name(key)
            strong = [name, key.title()] + [a for a in aliases if not a.isascii() or (len(a) <= 3 and a.isalpha())]
            pool = strong if strong_only else [a for a in aliases if a not in strong]
            hit = _earliest(text, pool)
            if hit and (best is None or hit[0] < best[0]):
                best = (hit[0], name, hit[1])
        if best:
            return best[1], best[2]
    return None


def detect_all_countries(text: str) -> dict:
    """{country: matched alias} for every known country mentioned in text."""
    found = {}
    for key, aliases in BUILTIN.items():
        hit = _earliest(text or "", [display_name(key), key.title()] + aliases)
        if hit:
            found[display_name(key)] = hit[1]
    return found