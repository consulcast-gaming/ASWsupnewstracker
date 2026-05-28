"""
Supply Chain Situation Tracker - fetcher

Reads feeds.json, fetches each RSS source, sends new articles to Qwen via
Groq for classification and structured extraction, merges with existing
events.json, writes the result back.

Designed to be idempotent: safe to run repeatedly. Articles are deduplicated
by stable hash of their URL. Existing events are pruned by date (events
older than PRUNE_AGE_DAYS are dropped each run), then new ones are appended.

Environment variables:
  GROQ_API_KEY    required, free tier OK. Stored in the repo as a secret
                  named GEMINI_API_KEY (legacy name); the workflow YAML maps
                  it to GROQ_API_KEY for this script.
  DRY_RUN         optional, if set to "1" skips the LLM calls and writes a
                  placeholder for each new candidate article (useful for
                  testing the pipeline before adding an API key).

Usage:
  python fetch.py
"""

import os
import json
import time
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser

# --- Configuration ---------------------------------------------------------

ROOT          = Path(__file__).parent
FEEDS_FILE    = ROOT / "feeds.json"
EVENTS_FILE   = ROOT / "events.json"
MAX_AGE_DAYS  = 7          # ignore incoming articles older than this
PRUNE_AGE_DAYS = 7         # delete stored events whose event-date is older than this
MAX_TOTAL     = 200        # cap total events kept (rolling window)
SLEEP_BETWEEN = 1          # polite delay between Groq calls (seconds)

CATEGORIES = ["chokepoint", "vessel", "port", "conflict",
              "policy", "labour", "route", "industry"]
SEVERITIES = ["critical", "high", "medium", "low", "info"]

PROMPT = """You are filtering a news article for a supply chain situation tracker.

The tracker shows ongoing disruptions and notable events relevant to global
freight, shipping, and logistics: port issues, vessel incidents, chokepoint
disruptions, trade policy moves, labour actions, route shifts, conflicts that
affect freight flows, and industry signals (carrier announcements, etc).

Reject anything that is: corporate earnings reporting, technology product
announcements, opinion columns, year-in-review summaries, or general
non-disruption-related news.

Article title: {title}
Article summary: {summary}
Article URL: {url}

If this article describes a SPECIFIC, CURRENT supply chain event or
disruption worth tracking, return ONLY this JSON (no other text, no
markdown fences):

{{
  "relevant": true,
  "title": "<concise event title, max 80 chars>",
  "location": "<specific location name>",
  "lat": <latitude as a number>,
  "lng": <longitude as a number>,
  "category": "<one of: {categories}>",
  "severity": "<one of: {severities}>",
  "description": "<2-3 sentence factual summary>"
}}

If the article is NOT relevant, return ONLY:
{{"relevant": false}}

Coordinates should be best-guess for the central location of the event.
Be precise about category and severity; do not default to "medium" or
"industry" if a more specific value fits."""


# --- Helpers ---------------------------------------------------------------

def stable_id(url: str) -> str:
    """Stable 12-char hash of an article URL — used as dedupe key."""
    return hashlib.md5(url.encode("utf-8")).hexdigest()[:12]


def parse_published(entry) -> datetime | None:
    """Best-effort extraction of an article's publish datetime (UTC)."""
    for attr in ("published_parsed", "updated_parsed"):
        v = getattr(entry, attr, None)
        if v:
            return datetime(*v[:6], tzinfo=timezone.utc)
    return None


def load_existing() -> dict:
    """Load existing events.json. If absent or broken, return a fresh shell."""
    if EVENTS_FILE.exists():
        try:
            with open(EVENTS_FILE) as f:
                data = json.load(f)
            if isinstance(data.get("events"), list):
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return {"lastUpdated": None, "events": []}


def prune_old_events(state: dict, max_age_days: int = PRUNE_AGE_DAYS) -> int:
    """Drop events whose 'date' field is more than max_age_days days old.

    The fetcher previously only filtered incoming articles by recency, which
    meant existing events accumulated until MAX_TOTAL pushed them out. The
    tracker is supposed to show *current* situations, so events more than a
    week old are stale and should be evicted. Returns the number of events
    removed.

    Events with missing or malformed dates are kept (safer to be conservative
    than to accidentally evict things that just had a parse error).
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).date()
    cutoff_str = cutoff.isoformat()
    before = len(state["events"])
    kept = []
    for e in state["events"]:
        d = e.get("date", "")
        if not d or not isinstance(d, str) or len(d) != 10:
            kept.append(e)
        elif d >= cutoff_str:
            kept.append(e)
    state["events"] = kept
    return before - len(kept)


def load_feeds() -> list[dict]:
    with open(FEEDS_FILE) as f:
        return json.load(f)["feeds"]


def call_gemini(client, title: str, summary: str, url: str) -> dict | None:
    """Send one article through Qwen via Groq, parse JSON response."""
    prompt = PROMPT.format(
        title=title[:200],
        summary=(summary or "")[:1500],
        url=url,
        categories=", ".join(CATEGORIES),
        severities=", ".join(SEVERITIES),
    )
    try:
        resp = client.chat.completions.create(
            model="qwen/qwen3-32b",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            reasoning_effort="none",
        )
        text = (resp.choices[0].message.content or "").strip()
    except Exception as exc:
        print(f"  gemini error: {exc}")
        return None

    # Strip optional code fences the model sometimes adds.
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        print(f"  bad json: {text[:120]}")
        return None

    if not data.get("relevant"):
        return None

    # Validate fields
    try:
        return {
            "title":       str(data["title"])[:120],
            "location":    str(data["location"])[:80],
            "lat":         float(data["lat"]),
            "lng":         float(data["lng"]),
            "category":    data["category"] if data["category"] in CATEGORIES else "industry",
            "severity":    data["severity"] if data["severity"] in SEVERITIES else "medium",
            "description": str(data["description"])[:600],
        }
    except (KeyError, ValueError, TypeError) as exc:
        print(f"  validation error: {exc}")
        return None


# --- Main ------------------------------------------------------------------

def main() -> int:
    dry_run = os.environ.get("DRY_RUN") == "1"

    # Set up Groq client unless dry run
    client = None
    if not dry_run:
        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            print("ERROR: GROQ_API_KEY env var not set. "
                  "Set it, or run with DRY_RUN=1 to test the pipeline.")
            return 1
        from groq import Groq
        client = Groq(api_key=api_key)

    feeds         = load_feeds()
    state         = load_existing()

    # Drop stale events whose date is older than PRUNE_AGE_DAYS. We do this
    # before building existing_ids so that if a story re-surfaces this week,
    # it can be re-added rather than being silently deduped against an old
    # copy we just intended to delete.
    pruned = prune_old_events(state)
    if pruned:
        cutoff_date = (datetime.now(timezone.utc) - timedelta(days=PRUNE_AGE_DAYS)).date()
        print(f"Pruned {pruned} events older than {PRUNE_AGE_DAYS} days "
              f"(date < {cutoff_date})")

    existing_ids  = {e["id"] for e in state["events"]}
    cutoff        = datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)
    added         = 0
    skipped_old   = 0
    skipped_dupe  = 0
    skipped_norel = 0

    for feed in feeds:
        feed_name = feed["name"]
        feed_url  = feed["url"]
        print(f"\n=== {feed_name} ===")
        try:
            parsed = feedparser.parse(feed_url)
        except Exception as exc:
            print(f"  fetch failed: {exc}")
            continue
        entries = parsed.entries or []
        print(f"  {len(entries)} items")
        for entry in entries:
            url = entry.get("link", "")
            if not url:
                continue
            eid = stable_id(url)
            if eid in existing_ids:
                skipped_dupe += 1
                continue
            pub = parse_published(entry)
            if pub and pub < cutoff:
                skipped_old += 1
                continue

            title   = entry.get("title", "(no title)")
            summary = entry.get("summary", entry.get("description", ""))

            print(f"  -> {title[:80]}")
            if dry_run:
                # Add as placeholder so we can see the pipeline working
                event = {
                    "id": eid,
                    "title": title[:80],
                    "location": "(dry run — Gemini not called)",
                    "lat": 0.0, "lng": 0.0,
                    "category": "industry",
                    "severity": "info",
                    "description": (summary[:300] or "(no summary)"),
                }
            else:
                extracted = call_gemini(client, title, summary, url)
                time.sleep(SLEEP_BETWEEN)
                if not extracted:
                    skipped_norel += 1
                    continue
                event = {"id": eid, **extracted}

            event_date = pub or datetime.now(timezone.utc)
            event.update({
                "date":        event_date.strftime("%Y-%m-%d"),
                "dateLabel":   event_date.strftime("%b %d, %Y"),
                "sourceUrl":   url,
                "sourceLabel": feed_name,
                "added":       datetime.now(timezone.utc).isoformat(),
            })
            state["events"].append(event)
            existing_ids.add(eid)
            added += 1

    # Trim to MAX_TOTAL most recently added
    state["events"].sort(key=lambda e: e.get("added", ""), reverse=True)
    state["events"] = state["events"][:MAX_TOTAL]
    state["lastUpdated"] = datetime.now(timezone.utc).isoformat()

    with open(EVENTS_FILE, "w") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

    print(f"\nDone. Added {added} events. "
          f"Skipped: {skipped_dupe} dupes, {skipped_old} old, "
          f"{skipped_norel} non-relevant. "
          f"Total now: {len(state['events'])}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
