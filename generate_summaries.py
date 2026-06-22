#!/usr/bin/env python3
"""
Generates today/7-day/30-day summaries from content.json using Claude Sonnet.
Output: bullet points sorted by importance, what's new, buy/sell/market divergences.
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
        lines.append(f"\n[{e['date']}] {e['investor']} ({side}, {e.get('sentiment','?').upper()})")
        lines.append(f"  View: {e.get('summary', '')}")
        if e.get("key_views"):
            lines.append("  Key points: " + " | ".join(e["key_views"]))
        if e.get("trade_ideas"):
            lines.append("  Trades: " + " | ".join(e["trade_ideas"]))
    return "\n".join(lines) if lines else "No content available."

def split_recent_older(entries: list[dict], recent_days: int) -> tuple[list, list]:
    """Split entries into recent (last N days) vs older within the period."""
    cutoff = (date.today() - timedelta(days=recent_days)).isoformat()
    recent = [e for e in entries if e["date"] >= cutoff]
    older  = [e for e in entries if e["date"] < cutoff]
    return recent, older

def generate_summary(entries: list[dict], period_label: str, period_days: int) -> dict:
    empty = {
        "buy_side_bullets": [],
        "sell_side_bullets": [],
        "whats_new": [],
        "vs_market_buy": [],
        "vs_market_sell": [],
        "buy_vs_sell_divergence": [],
        "aligned": [],
        "trade_ideas": [],
        "dominant_themes": [],
        "buy_side_sentiment": "mixed",
        "sell_side_sentiment": "mixed",
    }
    if not entries:
        return empty

    context = build_context(entries)
    buy_count  = sum(1 for e in entries if e["side"] == "buy")
    sell_count = sum(1 for e in entries if e["side"] == "sell")

    # For "what's new": compare last 1-2 days vs rest of period
    recent_days = 1 if period_days <= 1 else (2 if period_days <= 7 else 7)
    recent, older = split_recent_older(entries, recent_days)
    recent_context = build_context(recent) if recent else "None"
    older_context  = build_context(older)  if older  else "None"

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    prompt = f"""You are a senior macro research analyst synthesising views from top investors and strategists.

Period: {period_label}
Buy-side entries: {buy_count} | Sell-side entries: {sell_count}

ALL CONTENT FOR PERIOD:
{context}

MOST RECENT ENTRIES (last {recent_days} day(s)):
{recent_context}

OLDER ENTRIES IN PERIOD:
{older_context}

Produce a JSON synthesis with EXACTLY these fields. All bullet arrays must be sorted most-important first.

{{
  "buy_side_bullets": [
    "One-sentence bullet, specific and attributed. E.g. 'Druckenmiller cut Amazon 94%, rotating into semis as AI infrastructure play.' (most important first)",
    "...",
    "...",
    "...",
    "..."
  ],
  "sell_side_bullets": [
    "One-sentence bullet, specific and attributed. E.g. 'Hartnett (BofA) warns this is the biggest bubble since railroads, drawing parallels to 1999.' (most important first)",
    "...",
    "...",
    "...",
    "..."
  ],
  "whats_new": [
    "What has CHANGED or is NEWLY expressed vs the start of this period. Compare recent entries vs older entries. E.g. 'Dalio escalated his recession warning this week, moving from cautious to explicitly calling a 1929/2000-style bubble.' Only include genuine changes — omit if views are unchanged.",
    "...",
    "..."
  ],
  "vs_market_buy": [
    "Where buy-side practitioners DIFFER from what markets are currently pricing in. E.g. 'Druckenmiller is short mega-cap tech while markets still price in AI-driven multiple expansion.' Be specific about what market is pricing vs what the investor thinks.",
    "...",
    "..."
  ],
  "vs_market_sell": [
    "Where sell-side strategists DIFFER from market implied. E.g. 'Rosenberg sees stagflation risk while credit spreads remain tight, implying markets are complacent.' Be specific.",
    "...",
    "..."
  ],
  "buy_vs_sell_divergence": [
    "Where practitioners and strategists EXPLICITLY DISAGREE. E.g. 'Slok (sell-side) bullish on strong economy while Gundlach (buy-side) warns Fed won't cut under Warsh — opposite rate conclusions.' Most important divergence first.",
    "...",
    "..."
  ],
  "aligned": [
    "Where buy-side AND sell-side AGREE with each other (and note if market is also aligned or diverges). E.g. 'Both Dalio (buy-side) and Hartnett (sell-side) warn of bubble-like equity valuations — but market is still making new highs, suggesting markets disagree.' Most important consensus first.",
    "...",
    "..."
  ],
  "trade_ideas": [
    "1. Long/Short [specific asset]: [rationale in 1 sentence]. Source: [investor name]",
    "2. ...",
    "3. ...",
    "4. ...",
    "5. ..."
  ],
  "dominant_themes": ["theme 1", "theme 2", "theme 3"],
  "buy_side_sentiment": "bullish or bearish or neutral or mixed",
  "sell_side_sentiment": "bullish or bearish or neutral or mixed"
}}

Rules:
- Every bullet must name at least one investor
- Sort ALL arrays most-important first
- whats_new: only include genuine changes vs period start, skip if unchanged
- vs_market: use your knowledge of current market implied levels (equity valuations, rate expectations, credit spreads, vol) to anchor the comparison
- Return only valid JSON, no other text"""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4000,
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

    print(f"Entries — today: {len(today_entries)}, 7d: {len(week_entries)}, 30d: {len(month_entries)}")

    week_start  = (date.today() - timedelta(days=7)).isoformat()
    month_start = (date.today() - timedelta(days=30)).isoformat()

    print("Generating today summary...")
    today_summary = generate_summary(today_entries, f"Today ({TODAY})", 1)

    print("Generating 7-day summary...")
    week_summary = generate_summary(week_entries, f"Last 7 days ({week_start} to {TODAY})", 7)

    print("Generating 30-day summary...")
    month_summary = generate_summary(month_entries, f"Last 30 days ({month_start} to {TODAY})", 30)

    out = {
        "generated_at": TODAY,
        "today": {"date": TODAY, "entries_count": len(today_entries), **today_summary},
        "week":  {"period_start": week_start,  "period_end": TODAY, "entries_count": len(week_entries),  **week_summary},
        "month": {"period_start": month_start, "period_end": TODAY, "entries_count": len(month_entries), **month_summary},
    }

    os.makedirs("data", exist_ok=True)
    with open(SUMMARIES_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    print("Done.")

if __name__ == "__main__":
    main()
