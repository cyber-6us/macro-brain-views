#!/usr/bin/env python3
"""
Daily scraper for macro investor and strategist views.
Searches DuckDuckGo (text + news) for each tracked person.
Extracts key macro views and trade ideas using Claude Haiku.
Appends to data/content.json, pruning entries older than 90 days.
"""

import json
import os
import time
import hashlib
import requests
from bs4 import BeautifulSoup
from datetime import date, timedelta
from duckduckgo_search import DDGS
import anthropic

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CONTENT_FILE = "data/content.json"
TODAY = date.today().isoformat()
CUTOFF_90D = (date.today() - timedelta(days=90)).isoformat()

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
}
PAYWALLED = {"bloomberg.com", "ft.com", "wsj.com", "economist.com", "barrons.com",
             "reuters.com/plus", "nytimes.com"}

# ── Investor universe ──────────────────────────────────────────────────────────

BUY_SIDE = [
    {"name": "Stan Druckenmiller", "queries": ["Druckenmiller macro view", "Druckenmiller market"]},
    {"name": "Ray Dalio", "queries": ["Ray Dalio economy 2025 2026", "Dalio Bridgewater macro"]},
    {"name": "Paul Tudor Jones", "queries": ["Paul Tudor Jones market", "Tudor Jones macro outlook"]},
    {"name": "Howard Marks", "queries": ["Howard Marks memo market", "Howard Marks Oaktree"]},
    {"name": "George Soros", "queries": ["George Soros economy macro", "Soros market view"]},
    {"name": "David Tepper", "queries": ["David Tepper market", "Tepper Appaloosa positioning"]},
    {"name": "Jeffrey Gundlach", "queries": ["Gundlach bonds rates", "Jeffrey Gundlach macro outlook"]},
    {"name": "Kyle Bass", "queries": ["Kyle Bass macro economy", "Hayman Capital view"]},
    {"name": "Michael Burry", "queries": ["Michael Burry market portfolio", "Burry scion macro"]},
    {"name": "Alan Howard", "queries": ["Alan Howard macro Brevan", "Brevan Howard positioning"]},
    {"name": "Scott Bessent", "queries": ["Scott Bessent economy Treasury", "Bessent macro view"]},
    {"name": "Bill Ackman", "queries": ["Bill Ackman macro markets", "Ackman interest rates view"]},
    {"name": "David Einhorn", "queries": ["David Einhorn market Greenlight", "Einhorn macro positioning"]},
    {"name": "Crispin Odey", "queries": ["Crispin Odey macro market", "Odey fund view"]},
]

SELL_SIDE = [
    {"name": "Jim Reid", "queries": ["Jim Reid Deutsche Bank macro", "Jim Reid market chart"]},
    {"name": "Torsten Slok", "queries": ["Torsten Slok Apollo economy", "Slok macro outlook"]},
    {"name": "Michael Hartnett", "queries": ["Michael Hartnett BofA macro", "Hartnett flow show"]},
    {"name": "Albert Edwards", "queries": ["Albert Edwards SocGen macro", "Edwards ice age market"]},
    {"name": "David Rosenberg", "queries": ["David Rosenberg economy", "Rosenberg macro outlook"]},
    {"name": "Ed Yardeni", "queries": ["Ed Yardeni economy outlook", "Yardeni market view"]},
    {"name": "Jan Hatzius", "queries": ["Jan Hatzius Goldman economy", "Hatzius macro outlook"]},
    {"name": "Mike Wilson", "queries": ["Mike Wilson Morgan Stanley equity", "Wilson market outlook"]},
    {"name": "Russell Napier", "queries": ["Russell Napier macro economy", "Napier financial repression"]},
    {"name": "Peter Berezin", "queries": ["Peter Berezin BCA macro", "Berezin recession outlook"]},
    {"name": "Liz Ann Sonders", "queries": ["Liz Ann Sonders Schwab economy", "Sonders market view"]},
    {"name": "Barry Bannister", "queries": ["Barry Bannister Stifel equity", "Bannister market target"]},
]

# ── Helpers ────────────────────────────────────────────────────────────────────

def url_id(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()[:10]

def investor_day_id(name: str, day: str) -> str:
    return f"{day}-{hashlib.md5(name.encode()).hexdigest()[:8]}"

def is_paywalled(url: str) -> bool:
    from urllib.parse import urlparse
    domain = urlparse(url).netloc.lstrip("www.")
    return any(d in domain for d in PAYWALLED)

def fetch_article_text(url: str, max_chars: int = 1800) -> str | None:
    if is_paywalled(url):
        return None
    try:
        r = requests.get(url, headers=HEADERS, timeout=8)
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form"]):
            tag.decompose()
        text = " ".join(soup.get_text(" ").split())
        return text[:max_chars] if len(text) > 100 else None
    except Exception:
        return None

def ddg_search(query: str, search_type: str = "text", timelimit: str = "d", max_results: int = 3) -> list:
    try:
        with DDGS() as ddgs:
            if search_type == "news":
                return list(ddgs.news(query, timelimit=timelimit, max_results=max_results))
            return list(ddgs.text(query, timelimit=timelimit, max_results=max_results))
    except Exception:
        return []

def extract_with_claude(name: str, side: str, snippets: list[str]) -> dict | None:
    if not snippets or not ANTHROPIC_API_KEY:
        return None

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    side_label = "buy-side macro investor (practitioner who manages money)" if side == "buy" \
        else "sell-side macro strategist (research/commentary)"

    combined = "\n".join(f"- {s}" for s in snippets[:10])

    prompt = f"""Analyze these recent snippets about {name}, a {side_label}.

{combined}

Extract their macro views. Return JSON only:
{{
  "has_content": true or false,
  "summary": "2-3 sentence synthesis of their current macro view (be specific, cite what they said)",
  "key_views": ["specific view 1", "specific view 2", "specific view 3"],
  "trade_ideas": ["specific trade 1", "specific trade 2"],
  "asset_classes": ["equities"|"bonds"|"fx"|"commodities"|"credit"|"rates"],
  "sentiment": "bullish"|"bearish"|"neutral"|"mixed"
}}

Set has_content false if snippets contain no meaningful macro views (only generic news or unrelated content).
Return only valid JSON."""

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

# ── Core scrape per person ─────────────────────────────────────────────────────

def scrape_person(person: dict, side: str) -> dict | None:
    name = person["name"]
    snippets = []
    sources = []

    for query in person["queries"]:
        for result in ddg_search(query, "text", timelimit="w", max_results=4):
            url = result.get("href", result.get("url", ""))
            title = result.get("title", "")
            body = result.get("body", result.get("snippet", ""))
            if body:
                snippets.append(f"[{title}] {body}")
            if url and url not in sources:
                sources.append(url)
                article = fetch_article_text(url)
                if article:
                    snippets.append(f"[Article: {title}] {article[:900]}")
        time.sleep(2)

        for result in ddg_search(query, "news", timelimit="m", max_results=4):
            url = result.get("url", "")
            title = result.get("title", "")
            body = result.get("body", result.get("excerpt", ""))
            if body:
                snippets.append(f"[News: {title}] {body}")
            if url and url not in sources:
                sources.append(url)
        time.sleep(1.5)

    if not snippets:
        print(f"  {name}: no content found")
        return None

    print(f"  {name}: {len(snippets)} snippets — extracting...")
    extracted = extract_with_claude(name, side, snippets)

    if not extracted or not extracted.get("has_content"):
        print(f"  {name}: no meaningful views today")
        return None

    print(f"  {name}: {extracted.get('sentiment','?')} — {extracted.get('summary','')[:80]}...")
    return {
        "id": investor_day_id(name, TODAY),
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

if __name__ == "__main__":
    main()
