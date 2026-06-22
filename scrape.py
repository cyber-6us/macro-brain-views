#!/usr/bin/env python3
"""
Daily scraper for macro investor and strategist views.
Primary source: Google News RSS (free, no API key, Google-quality results).
Backup source: DuckDuckGo (used only when RSS returns nothing).
Extracts key macro views and trade ideas using Claude Haiku.
Appends to data/content.json, pruning entries older than 90 days.
"""

import json
import os
import time
import hashlib
import requests
import xml.etree.ElementTree as ET
from urllib.parse import quote_plus
from bs4 import BeautifulSoup
from datetime import date, timedelta
import anthropic

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CONTENT_FILE = "data/content.json"
TODAY = date.today().isoformat()
CUTOFF_90D = "2000-01-01"  # keep all entries indefinitely

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}
PAYWALLED = {
    "bloomberg.com", "ft.com", "wsj.com", "economist.com",
    "barrons.com", "nytimes.com", "thetimes.co.uk"
}

# ── Investor universe ──────────────────────────────────────────────────────────

BUY_SIDE = [
    {"name": "Stan Druckenmiller", "terms": ["Druckenmiller macro", "Druckenmiller economy market"]},
    {"name": "Ray Dalio",          "terms": ["Ray Dalio macro economy", "Dalio Bridgewater"]},
    {"name": "Paul Tudor Jones",   "terms": ["Paul Tudor Jones macro", "Tudor Jones market"]},
    {"name": "Howard Marks",       "terms": ["Howard Marks Oaktree memo", "Howard Marks market"]},
    {"name": "George Soros",       "terms": ["George Soros economy macro", "Soros market view"]},
    {"name": "David Tepper",       "terms": ["David Tepper market", "Tepper Appaloosa"]},
    {"name": "Jeffrey Gundlach",   "terms": ["Gundlach bonds rates", "Jeffrey Gundlach macro"]},
    {"name": "Kyle Bass",          "terms": ["Kyle Bass macro economy", "Hayman Capital"]},
    {"name": "Michael Burry",      "terms": ["Michael Burry market", "Burry Scion macro"]},
    {"name": "Alan Howard",        "terms": ["Alan Howard Brevan macro", "Brevan Howard market"]},
    {"name": "Scott Bessent",      "terms": ["Scott Bessent economy", "Bessent macro Treasury"]},
    {"name": "Bill Ackman",        "terms": ["Bill Ackman macro rates", "Ackman market view"]},
    {"name": "David Einhorn",      "terms": ["David Einhorn market", "Greenlight Capital macro"]},
    {"name": "Crispin Odey",       "terms": ["Crispin Odey macro", "Odey fund market"]},
]

SELL_SIDE = [
    {"name": "Jim Reid",          "terms": ["Jim Reid Deutsche Bank macro", "Jim Reid market"]},
    {"name": "Torsten Slok",      "terms": ["Torsten Slok Apollo economy", "Slok macro"]},
    {"name": "Michael Hartnett",  "terms": ["Michael Hartnett BofA macro", "Hartnett flow show"]},
    {"name": "Albert Edwards",    "terms": ["Albert Edwards SocGen macro", "Albert Edwards ice age"]},
    {"name": "David Rosenberg",   "terms": ["David Rosenberg economy macro", "Rosenberg Research"]},
    {"name": "Ed Yardeni",        "terms": ["Ed Yardeni economy outlook", "Yardeni market"]},
    {"name": "Jan Hatzius",       "terms": ["Jan Hatzius Goldman economy", "Hatzius macro"]},
    {"name": "Mike Wilson",       "terms": ["Mike Wilson Morgan Stanley equity", "Wilson market outlook"]},
    {"name": "Russell Napier",    "terms": ["Russell Napier macro economy", "Napier financial repression"]},
    {"name": "Peter Berezin",     "terms": ["Peter Berezin BCA macro", "Berezin recession outlook"]},
    {"name": "Liz Ann Sonders",   "terms": ["Liz Ann Sonders Schwab economy", "Sonders market"]},
    {"name": "Barry Bannister",   "terms": ["Barry Bannister Stifel equity", "Bannister market"]},
    {"name": "Jordi Visser",      "terms": ["Jordi Visser 22V Research macro", "Jordi Visser market outlook"]},
    {"name": "John Mauldin",      "terms": ["John Mauldin macro economy", "Mauldin Economics outlook"],
                                   "urls": ["https://www.mauldineconomics.com/frontlinethoughts",
                                            "https://www.mauldineconomics.com/"]},
]

# ── Google News RSS ────────────────────────────────────────────────────────────

def google_news_rss(query: str, period: str = "30d", max_results: int = 5) -> list[dict]:
    """Fetch Google News RSS — free, no API key, Google-quality results."""
    encoded = quote_plus(f'{query} when:{period}')
    url = f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"
    try:
        r = requests.get(url, headers=HEADERS, timeout=12)
        root = ET.fromstring(r.content)
        results = []
        for item in root.findall(".//item")[:max_results]:
            title   = item.findtext("title", "").strip()
            link    = item.findtext("link", "").strip()
            snippet = BeautifulSoup(item.findtext("description", ""), "html.parser").get_text()
            source  = item.findtext("source", "")
            if title:
                results.append({"title": title, "url": link, "snippet": snippet, "source": source})
        return results
    except Exception:
        return []

def resolve_google_url(url: str) -> str:
    """Follow Google News redirect to get the actual article URL."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=8, allow_redirects=True)
        return r.url
    except Exception:
        return url

# ── Article fetching ───────────────────────────────────────────────────────────

def is_paywalled(url: str) -> bool:
    from urllib.parse import urlparse
    domain = urlparse(url).netloc.lstrip("www.")
    return any(d in domain for d in PAYWALLED)

def fetch_article_text(url: str, max_chars: int = 1800) -> str | None:
    if not url or is_paywalled(url):
        return None
    try:
        r = requests.get(url, headers=HEADERS, timeout=8)
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form"]):
            tag.decompose()
        text = " ".join(soup.get_text(" ").split())
        return text[:max_chars] if len(text) > 150 else None
    except Exception:
        return None

# ── DuckDuckGo backup ─────────────────────────────────────────────────────────

def ddg_search(query: str, max_results: int = 3) -> list[dict]:
    """DuckDuckGo fallback — used only when RSS returns nothing."""
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(query, timelimit="m", max_results=max_results))
        return [{"title": r.get("title",""), "url": r.get("href",""), "snippet": r.get("body","")} for r in results]
    except Exception:
        return []

# ── Claude extraction ─────────────────────────────────────────────────────────

def extract_with_claude(name: str, side: str, snippets: list[str]) -> dict | None:
    if not snippets or not ANTHROPIC_API_KEY:
        return None

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    side_label = ("buy-side macro investor (practitioner who manages real money)"
                  if side == "buy" else "sell-side macro strategist (research/commentary)")

    combined = "\n".join(f"- {s}" for s in snippets[:12])

    prompt = f"""Analyse these recent snippets about {name}, a {side_label}.

{combined}

Extract their macro views. Return JSON only:
{{
  "has_content": true or false,
  "summary": "2-3 sentence synthesis of their current macro view — be specific, cite what they said",
  "key_views": ["specific view 1", "specific view 2", "specific view 3"],
  "trade_ideas": ["specific trade 1", "specific trade 2"],
  "asset_classes": ["equities","bonds","fx","commodities","credit","rates"],
  "sentiment": "bullish" | "bearish" | "neutral" | "mixed"
}}

Set has_content false if snippets contain no meaningful macro views.
Return only valid JSON, no other text."""

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=700,
            messages=[{"role": "user", "content": prompt}]
        )
        text = response.content[0].text.strip()
        if "```" in text:
            text = text.split("```")[1].lstrip("json").strip()
        return json.loads(text)
    except Exception:
        return None

# ── Core scrape per person ─────────────────────────────────────────────────────

def scrape_person(person: dict, side: str) -> dict | None:
    name = person["name"]
    snippets = []
    sources = []

    # Direct URLs (for investors who publish on their own site)
    for url in person.get("urls", []):
        article = fetch_article_text(url, max_chars=2000)
        if article:
            snippets.append(f"[Direct: {url}] {article}")
        if url not in sources:
            sources.append(url)

    # Primary: Google News RSS (2 search terms, recent 30 days)
    for term in person["terms"]:
        for result in google_news_rss(term, period="30d", max_results=5):
            url = result["url"]
            title = result["title"]
            snippet = result["snippet"]

            if snippet:
                snippets.append(f"[{title}] {snippet}")

            if url and url not in sources:
                sources.append(url)
                actual_url = resolve_google_url(url)
                article = fetch_article_text(actual_url)
                if article:
                    snippets.append(f"[Article: {title}] {article[:1000]}")

        time.sleep(1)  # be polite to Google RSS

    # Backup: DuckDuckGo if RSS returned nothing
    if not snippets:
        print(f"  {name}: RSS empty — trying DuckDuckGo...")
        for term in person["terms"][:1]:
            for result in ddg_search(term):
                url = result["url"]
                if result["snippet"]:
                    snippets.append(f"[{result['title']}] {result['snippet']}")
                if url and url not in sources:
                    sources.append(url)
                    article = fetch_article_text(url)
                    if article:
                        snippets.append(f"[Article: {result['title']}] {article[:800]}")
            time.sleep(3)

    if not snippets:
        print(f"  {name}: no content found")
        return None

    print(f"  {name}: {len(snippets)} snippets — extracting...")
    extracted = extract_with_claude(name, side, snippets)

    if not extracted or not extracted.get("has_content"):
        print(f"  {name}: no meaningful views in snippets")
        return None

    print(f"  {name}: {extracted.get('sentiment','?')} — {extracted.get('summary','')[:80]}...")
    return {
        "id": f"{TODAY}-{hashlib.md5(name.encode()).hexdigest()[:8]}",
        "date": TODAY,
        "investor": name,
        "side": side,
        "summary": extracted.get("summary", ""),
        "key_views": extracted.get("key_views", []),
        "trade_ideas": extracted.get("trade_ideas", []),
        "asset_classes": extracted.get("asset_classes", []),
        "sentiment": extracted.get("sentiment", "mixed"),
        "sources": sources[:4],
    }

# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    print(f"=== Macro Brain daily scrape — {TODAY} ===\n")
    data = load_content()
    existing_ids = {e["id"] for e in data["entries"]}
    new_entries = []

    print("--- Buy-side practitioners ---")
    for person in BUY_SIDE:
        entry = scrape_person(person, "buy")
        if entry and entry["id"] not in existing_ids:
            new_entries.append(entry)
            existing_ids.add(entry["id"])

    print("\n--- Sell-side strategists ---")
    for person in SELL_SIDE:
        entry = scrape_person(person, "sell")
        if entry and entry["id"] not in existing_ids:
            new_entries.append(entry)
            existing_ids.add(entry["id"])

    data["entries"].extend(new_entries)
    save_content(data)
    print(f"\nDone. Added {len(new_entries)} entries. Total in store: {len(data['entries'])}.")

def load_content() -> dict:
    if os.path.exists(CONTENT_FILE):
        with open(CONTENT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"last_updated": TODAY, "entries": []}

def save_content(data: dict) -> None:
    data["entries"] = [e for e in data["entries"] if e["date"] >= CUTOFF_90D]
    data["last_updated"] = TODAY
    os.makedirs("data", exist_ok=True)
    with open(CONTENT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

if __name__ == "__main__":
    main()
