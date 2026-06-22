#!/usr/bin/env python3
"""
Generates today/7-day/30-day summaries from content.json using Claude Sonnet.
Each summary breaks out buy-side vs sell-side views, key divergences, and top trade ideas.
Writes structured JSON to data/summaries.json.
"""

import json
import os
from datetime import date, timedelta
import anthropic

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CONTENT_FILE = "data/content.json"
SUMMARIES_FILE = "data/summaries.json"
TODAY = date.today().isoformat()

def load_content() -> list[dict]:
    if not os.path.exists(CONTENT_FILE):
        return []
    with open(CONTENT_FILE, "r", encoding="utf-8") as f:
        return json.load(f).get("entries", [])

def filter_entries(entries: list[dict], days: int) -> list[dict]:
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    return [e for e in entries if e["date"] >= cutoff]

def build_context(entries: list[dict]) -> str:
    lines = []
    for e in sorted(entries, key=lambda x: x["date"], reverse=True):
        side = "BUY-SIDE" if e["side"] == "buy" else "SELL-SIDE"
        lines.append(f"\n[{e['date']}] {e['investor']} ({side}, {e.get('sentiment', '?').upper()})")
        lines.append(f"  View: {e.get('summary', '')}")
        if e.get("key_views"):
            lines.append("  Key points: " + " | ".join(e["key_views"]))
        if e.get("trade_ideas"):
            lines.append("  Trades: " + " | ".join(e["trade_ideas"]))
    return "\n".join(lines) if lines else "No content available."

def generate_summary(entries: list[dict], period_label: str) -> dict:
    empty = {
        "buy_side_theme": "No content available for this period yet.",
        "sell_side_theme": "No content available for this period yet.",
        "key_divergence": "—",
        "trade_ideas": [],
        "dominant_themes": [],
        "buy_side_sentiment": "mixed",
        "sell_side_sentiment": "mixed",
    }

    if not entries:
        return empty

    context = build_context(entries)
    buy_count = sum(1 for e in entries if e["side"] == "buy")
    sell_count = sum(1 for e in entries if e["side"] == "sell")

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    prompt = f"""You are a senior macro research analyst synthesizing views from top investors and strategists.

Period: {period_label}
Buy-side entries: {buy_count} | Sell-side entries: {sell_count}

Content:
{context}

Produce a synthesis as JSON with exactly these fields:
{{
  "buy_side_theme": "2-3 sentence synthesis of what buy-side practitioners (who manage real money) are currently thinking and positioning. Be specific — name investors, cite their actual views, note what they are long/short/avoiding.",
  "sell_side_theme": "2-3 sentence synthesis of what sell-side strategists are saying. Be specific — note any recession calls, targets, major themes across the strategists.",
  "key_divergence": "1-2 sentences on the most important gap between what practitioners are doing vs what strategists are saying. This is the editorial highlight — make it punchy and specific.",
  "trade_ideas": [
    "1. [Long/Short] [asset]: [rationale]. Source: [investor name]",
    "2. ...",
    "3. ...",
    "4. ...",
    "5. ..."
  ],
  "dominant_themes": ["theme 1", "theme 2", "theme 3"],
  "buy_side_sentiment": "bullish" or "bearish" or "neutral" or "mixed",
  "sell_side_sentiment": "bullish" or "bearish" or "neutral" or "mixed"
}}

Rules:
- Always attribute views to named investors (e.g. "Druckenmiller is long...", "Hartnett warns...")
- Trade ideas must be specific and actionable, not generic ("long S&P" is fine if that's what they said, "long equities" is too vague)
- If fewer than 3 distinct trade ideas exist, include only what's supported — do not invent
- Return only valid JSON, no other text"""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1400,
            messages=[{"role": "user", "content": prompt}]
        )
        text = response.content[0].text.strip()
        if "```" in text:
            text = text.split("```")[1].lstrip("json").strip()
        return json.loads(text)
    except Exception as e:
        print(f"  Error: {e}")
        return empty

def main():
    print(f"=== Generating summaries — {TODAY} ===\n")
    entries = load_content()

    today_entries = filter_entries(entries, 1)
    week_entries  = filter_entries(entries, 7)
    month_entries = filter_entries(entries, 30)

    print(f"Entries available — today: {len(today_entries)}, 7d: {len(week_entries)}, 30d: {len(month_entries)}")

    week_start  = (date.today() - timedelta(days=7)).isoformat()
    month_start = (date.today() - timedelta(days=30)).isoformat()

    print("Generating today summary...")
    today_summary = generate_summary(today_entries, f"Today ({TODAY})")

    print("Generating 7-day summary...")
    week_summary = generate_summary(week_entries, f"Last 7 days ({week_start} to {TODAY})")

    print("Generating 30-day summary...")
    month_summary = generate_summary(month_entries, f"Last 30 days ({month_start} to {TODAY})")

    out = {
        "generated_at": TODAY,
        "today": {"date": TODAY, "entries_count": len(today_entries), **today_summary},
        "week":  {"period_start": week_start,  "period_end": TODAY, "entries_count": len(week_entries),  **week_summary},
        "month": {"period_start": month_start, "period_end": TODAY, "entries_count": len(month_entries), **month_summary},
    }

    os.makedirs("data", exist_ok=True)
    with open(SUMMARIES_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    print("Done. Summaries saved to data/summaries.json.")

if __name__ == "__main__":
    main()
