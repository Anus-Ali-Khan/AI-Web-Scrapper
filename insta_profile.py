"""Stage 2: open a public profile and read the numbers/bio from its meta tags.
Meta tags are far more stable than Instagram's obfuscated CSS classes."""
import json
import re

from bs4 import BeautifulSoup


def _meta(soup: BeautifulSoup, prop: str) -> str:
    tag = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
    return tag["content"].strip() if tag and tag.has_attr("content") else ""


def _to_int(text: str) -> int | None:
    """'1,234' -> 1234, '12.5K' -> 12500, '1.2M' -> 1200000"""
    s = text.replace(",", "").strip()
    if not s:
        return None
    mult = 1
    if s[-1].upper() in "KMB":
        mult = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}[s[-1].upper()]
        s = s[:-1]
    try:
        return int(float(s) * mult)
    except ValueError:
        return None


def _stat(desc: str, label: str) -> int | None:
    m = re.search(rf"([\d.,]+\s?[KMBkmb]?)\s+{label}", desc)
    return _to_int(m.group(1).replace(" ", "")) if m else None


def _external_url(html: str) -> str:
    """The bio link, if Instagram embedded it in the page source (not guaranteed when logged out)."""
    for pat in (r'"external_url"\s*:\s*"([^"]+)"', r'"bio_links"\s*:\s*\[\s*\{[^}]*?"url"\s*:\s*"([^"]+)"'):
        m = re.search(pat, html)
        if m:
            try:
                return json.loads('"' + m.group(1) + '"')
            except ValueError:
                return m.group(1)
    return ""


def is_login_wall(html: str, current_url: str) -> bool:
    return "accounts/login" in current_url or 'property="og:description"' not in html


def scrape_profile(browser, username: str) -> dict | None:
    """Returns a dict of profile info, or None if blocked / not available."""
    url = f"https://www.instagram.com/{username}/"
    html = browser.get_html(url, 4, 7)

    if is_login_wall(html, browser.current_url):
        return None

    soup = BeautifulSoup(html, "html.parser")
    og_desc = _meta(soup, "og:description")
    og_title = _meta(soup, "og:title")
    if not og_desc or "page not found" in og_title.lower():
        return None

    full_name = ""
    m = re.match(r"^(.*?)\s*\(@", og_title)
    if m:
        full_name = m.group(1).strip()

    bio = ""
    m = re.search(r'on Instagram:\s*[\u201c"](.*)[\u201d"]\s*$', og_desc, re.DOTALL)
    if m:
        bio = m.group(1).strip()

    return {
        "username": username,
        "url": url,
        "full_name": full_name,
        "followers": _stat(og_desc, "Followers"),
        "following": _stat(og_desc, "Following"),
        "posts": _stat(og_desc, "Posts"),
        "bio": bio,
        "profile_image": _meta(soup, "og:image"),
        "external_url": _external_url(html),
        "raw_description": og_desc,  # kept so the LLM can still use it
    }