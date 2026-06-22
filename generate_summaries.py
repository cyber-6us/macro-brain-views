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
        "buy_side_trades": [],
        "sell_side_trades": [],
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
Tracked sell-side: Jim Reid (DB), Torsten Slok (Apollo), Michael Hartnett (BofA), Albert Edwards (SocGen), David Rosenberg, Ed Yardeni, Jan Hatzius (GS), Mike Wilson (MS), Russell Napier, Peter Berezin (BCA), Liz Ann Sonders (Schwab), Barry Bannister (Stifel), Jordi Visser (22V Research).
Buy-side entries: {buy_count} | Sell-side entries: {sell_count}

ALL CONTENT FOR PERIOD:
{context}

MOST RECENT ENTRIES (last {recent_days} day(s)):
{recent_context}

OLDER ENTRIES IN PERIOD:
{older_context}

Produce a JSON synthesis with EXACTLY these fields.

STRICT RULES — read before writing a single word:
- Every array: 3 to 5 items maximum, sorted by MARKET IMPORTANCE (most price-moving first)
- Every bullet: under 20 words, punchy, no filler — name the investor, state the view, done
- whats_new: only real changes vs period start — omit entirely if nothing changed
- vs_market: anchor to current market pricing (equity multiples, rate expectations, credit spreads)
- Return ONLY valid JSON — no markdown, no explanation

{{
  "buy_side_bullets": [
    "Druckenmiller cut Amazon 94%, rotating into semis — AI infra over software.",
    "Dalio: US equities near 1929/2000 bubble levels, cutting risk.",
    "3rd most important buy-side view, attributed."
  ],
  "sell_side_bullets": [
    "Hartnett (BofA): biggest bubble since railroads, parallels 1999.",
    "Slok (Apollo): economy running hot, no cuts needed.",
    "3rd most important sell-side view, attributed."
  ],
  "whats_new": [
    "Genuine change vs period start only. E.g. Dalio moved from cautious to outright bubble call this week.",
    "2nd change if any."
  ],
  "vs_market_buy": [
    "Buy-side view vs what market prices. E.g. Burry short growth while market prices 20x forward PE.",
    "2nd divergence if material."
  ],
  "vs_market_sell": [
    "Sell-side view vs market. E.g. Rosenberg sees stagflation; credit spreads at 2-year tights disagree.",
    "2nd divergence if material."
  ],
  "buy_vs_sell_divergence": [
    "Biggest practitioner vs strategist disagreement. E.g. Gundlach (buy) no cuts vs Slok (sell) strong economy needs none — same conclusion, opposite reasoning.",
    "2nd divergence if material."
  ],
  "aligned": [
    "Where both camps agree. E.g. Dalio + Hartnett both call bubble — market disagrees, still rallying.",
    "2nd consensus if any."
  ],
  "buy_side_trades": [
    "Short mega-cap tech (Nvidia, Microsoft). Source: Druckenmiller, Burry.",
    "Long gold / BTC as inflation hedge. Source: Tudor Jones.",
    "3rd trade if supported."
  ],
  "sell_side_trades": [
    "Underweight equities, overweight cash. Source: Hartnett (BofA).",
    "Long duration bonds on recession risk. Source: Rosenberg.",
    "3rd trade if supported."
  ],
  "dominant_themes": ["AI bubble valuation", "Fed path uncertainty", "inflation regime shift"],
  "buy_side_sentiment": "bullish or bearish or neutral or mixed",
  "sell_side_sentiment": "bullish or bearish or neutral or mixed"
}}"""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=8000,
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
