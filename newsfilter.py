#!/usr/bin/env python3
"""24/7-Newsfilter für Bitcoin-Daytrading.

Läuft alle paar Minuten (GitHub Actions), liest Krypto-News per RSS und den
Wirtschaftskalender, lässt neue Meldungen von Claude bewerten und schickt
wichtige per ntfy aufs Handy und den Mac.

Umgebungsvariablen:
  ANTHROPIC_API_KEY   API-Schlüssel (GitHub-Secret)
  NTFY_TOPIC          geheimer ntfy-Kanalname (GitHub-Secret)
  MIN_IMPORTANCE      ab welcher Wichtigkeit (1–5) gemeldet wird, Standard 4
  CLAUDE_MODEL        Modell, Standard claude-haiku-5-5
  CLAUDE_EFFORT       low | medium | high, Standard low

Lokal testen ohne KI und ohne Mitteilungen:
  python newsfilter.py --dry-run
"""
import argparse
import datetime as dt
import html
import json
import os
import re
import sys
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

import feedparser

STATE_FILE = Path(__file__).resolve().parent / "state.json"
TZ = ZoneInfo("Europe/Berlin")
UA = "Mozilla/5.0 (btc-newsfilter)"

FEEDS = {
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss",
    "Cointelegraph": "https://cointelegraph.com/rss",
    "The Block": "https://www.theblock.co/rss.xml",
    "Decrypt": "https://decrypt.co/feed",
    "Bitcoin Magazine": "https://bitcoinmagazine.com/.rss/full/",
}
CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
CALENDAR_REFRESH = dt.timedelta(hours=1)   # Kalender nur stündlich abrufen (Anbieter bittet darum)
CALENDAR_WARN = dt.timedelta(minutes=30)   # so lange vor einem Termin warnen
MAX_AGE = dt.timedelta(hours=3)            # ältere Meldungen ignorieren
KEEP_SEEN = dt.timedelta(days=4)
DEFAULT_MODEL = "claude-haiku-5-5"  # Schlagzeilen bewerten ist eine einfache Aufgabe; günstig bei ~100 Läufen/Tag
FALLBACK_MODELS = ("claude-opus-5-5", "claude-fable-5-1", "claude-sonnet-5-5")  # unterstützen fallbacks="default"

SYSTEM_PROMPT = """Du bewertest Nachrichten für einen Bitcoin-Daytrader (BTC/USD-CFDs, Haltedauer Minuten bis Stunden).

Bewerte jede Meldung danach, wie wahrscheinlich sie den Bitcoin-Kurs in den nächsten Stunden spürbar bewegt:
5 = sehr wahrscheinlich starke Bewegung (z. B. überraschende Fed-/SEC-Entscheidung, großer Börsen-Hack, ETF-Genehmigung oder -Ablehnung, Staat kauft/verkauft große BTC-Bestände, massive Liquidationen)
4 = wahrscheinlich spürbare Bewegung (z. B. große ETF-Zu-/Abflüsse, wichtige Regulierungsnachricht, große Wal- oder Unternehmensbewegungen, Ausfall einer großen Börse)
3 = möglicher Einfluss, eher Stimmung
2 = kaum Einfluss (Altcoin-Nachrichten, allgemeine Branchennews)
1 = kein Einfluss (Meinungsartikel, Werbung, Kursrückblicke, Erklärartikel)

Kursrückblicke („BTC fiel um 3 %“) beschreiben Vergangenes und sind höchstens 2, außer sie melden ein neues Ereignis.
Richtung: "bullish", "bearish" oder "neutral" für den BTC-Kurs.
Zusammenfassung: ein kurzer deutscher Satz mit dem Kern der Meldung, ohne Floskeln.
Gib für jede übergebene id genau einen Eintrag zurück."""

SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "importance": {"type": "integer", "enum": [1, 2, 3, 4, 5]},
                    "direction": {"type": "string", "enum": ["bullish", "bearish", "neutral"]},
                    "summary_de": {"type": "string"},
                },
                "required": ["id", "importance", "direction", "summary_de"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}


def now_utc():
    return dt.datetime.now(dt.timezone.utc)


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"seen": {}, "calendar": [], "calendar_fetched": None, "warned": []}


def save_state(state):
    cutoff = (now_utc() - KEEP_SEEN).isoformat()
    state["seen"] = {k: v for k, v in state["seen"].items() if v >= cutoff}
    STATE_FILE.write_text(json.dumps(state, indent=1, ensure_ascii=False, sort_keys=True) + "\n")


def clean(text, limit=500):
    text = re.sub(r"<[^>]+>", " ", html.unescape(text or ""))
    return re.sub(r"\s+", " ", text).strip()[:limit]


def fetch_news():
    items = []
    for source, url in FEEDS.items():
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/rss+xml, application/xml"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                feed = feedparser.parse(resp.read())
        except Exception as exc:
            print(f"Feed {source} nicht abrufbar: {exc}", file=sys.stderr)
            continue
        if feed.bozo and not feed.entries:
            print(f"Feed {source} nicht lesbar: {feed.bozo_exception}", file=sys.stderr)
            continue
        for e in feed.entries:
            t = e.get("published_parsed") or e.get("updated_parsed")
            published = dt.datetime(*t[:6], tzinfo=dt.timezone.utc) if t else None
            items.append({
                "id": e.get("id") or e.get("link"),
                "source": source,
                "title": clean(e.get("title"), 300),
                "summary": clean(e.get("summary")),
                "link": e.get("link"),
                "published": published,
            })
    return [i for i in items if i["id"] and i["title"]]


def classify(items):
    """Bewertet Meldungen in einem einzigen Aufruf. Gibt {id: Bewertung} zurück."""
    import anthropic

    client = anthropic.Anthropic()
    model = os.environ.get("CLAUDE_MODEL") or DEFAULT_MODEL
    lines = [{"id": str(n), "quelle": i["source"], "titel": i["title"], "text": i["summary"]}
             for n, i in enumerate(items)]
    params = dict(
        model=model,
        max_tokens=16000,
        output_config={
            "effort": os.environ.get("CLAUDE_EFFORT") or "low",
            "format": {"type": "json_schema", "schema": SCHEMA},
        },
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": json.dumps(lines, ensure_ascii=False)}],
    )
    if model.startswith(FALLBACK_MODELS):
        # bei einer Ablehnung übernimmt serverseitig automatisch ein anderes Modell
        response = client.beta.messages.create(
            betas=["server-side-fallback-2026-07-01"], extra_body={"fallbacks": "default"}, **params)
    else:
        response = client.messages.create(**params)
    if response.stop_reason == "refusal":
        print(f"Bewertung abgelehnt: {response.stop_details}", file=sys.stderr)
        return {}
    text = next(b.text for b in response.content if b.type == "text")
    rated = json.loads(text)["items"]
    return {items[int(r["id"])]["id"]: r for r in rated if r["id"].isdigit() and int(r["id"]) < len(items)}


def notify(title, message, priority=3, tags=(), click=None, dry_run=False):
    if dry_run:
        print(f"  [Mitteilung p{priority}] {title} — {message}")
        return
    body = {"topic": os.environ["NTFY_TOPIC"], "title": title, "message": message,
            "priority": priority, "tags": list(tags)}
    if click:
        body["click"] = click
    req = urllib.request.Request("https://ntfy.sh/", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=20):
        pass


def run_news(state, dry_run, min_importance):
    items = fetch_news()
    first_run = not state["seen"]
    fresh = [i for i in items if i["id"] not in state["seen"]
             and (i["published"] is None or now_utc() - i["published"] <= MAX_AGE)]
    # Dieselbe Meldung kommt manchmal in mehreren Feeds: nach Titel entdoppeln
    unique = list({i["title"].lower(): i for i in fresh}.values())
    print(f"{len(items)} Meldungen gelesen, {len(unique)} neu")

    if first_run:  # beim allerersten Lauf nicht mit alten Meldungen fluten
        for i in items:
            state["seen"][i["id"]] = now_utc().isoformat()
        notify("Newsfilter aktiv", f"Überwacht {len(FEEDS)} Krypto-Quellen und US-Wirtschaftstermine.",
               tags=["white_check_mark"], dry_run=dry_run)
        return
    if not unique:
        return

    if dry_run and not os.environ.get("ANTHROPIC_API_KEY"):
        for i in unique:
            print(f"  (ohne KI) {i['source']}: {i['title']}")
        return

    ratings = classify(unique)
    for i in unique:
        r = ratings.get(i["id"])
        if r is None:
            continue  # nicht bewertet: beim nächsten Lauf erneut versuchen
        for other in fresh:
            if other["title"].lower() == i["title"].lower():
                state["seen"][other["id"]] = now_utc().isoformat()
        print(f"  {r['importance']} {r['direction']:8} {i['source']}: {i['title']}")
        if r["importance"] >= min_importance:
            arrow = {"bullish": "📈", "bearish": "📉"}.get(r["direction"], "➖")
            notify(f"{arrow} BTC {r['importance']}/5 · {i['source']}", f"{r['summary_de']}\n\n{i['title']}",
                   priority=5 if r["importance"] == 5 else 4,
                   tags=[{"bullish": "chart_with_upwards_trend", "bearish": "chart_with_downwards_trend"}
                         .get(r["direction"], "newspaper")],
                   click=i["link"], dry_run=dry_run)


def run_calendar(state, dry_run):
    fetched = state.get("calendar_fetched")
    if not fetched or now_utc() - dt.datetime.fromisoformat(fetched) >= CALENDAR_REFRESH:
        req = urllib.request.Request(CALENDAR_URL, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                events = json.loads(resp.read())
            state["calendar"] = [e for e in events if e.get("country") == "USD" and e.get("impact") == "High"]
            state["calendar_fetched"] = now_utc().isoformat()
        except Exception as exc:  # Kalender ist Zusatz – Fehler nicht den ganzen Lauf abbrechen lassen
            print(f"Kalender nicht abrufbar: {exc}", file=sys.stderr)
            # in 15 Minuten erneut versuchen statt bei jedem Lauf (vermeidet weitere 429-Fehler)
            state["calendar_fetched"] = (now_utc() - CALENDAR_REFRESH + dt.timedelta(minutes=15)).isoformat()

    for e in state["calendar"]:
        when = dt.datetime.fromisoformat(e["date"])
        key = f"{e['date']}|{e['title']}"
        if key in state["warned"] or not (dt.timedelta(0) <= when - now_utc() <= CALENDAR_WARN):
            continue
        minutes = round((when - now_utc()).total_seconds() / 60)
        details = ", ".join(x for x in (f"Prognose {e['forecast']}" if e.get("forecast") else "",
                                        f"vorher {e['previous']}" if e.get("previous") else "") if x)
        notify(f"⏰ In {minutes} Min: {e['title']} (USD)",
               f"{when.astimezone(TZ):%H:%M} Uhr · hoher Einfluss{(' · ' + details) if details else ''}. "
               "Mit starken Ausschlägen und breiterem Spread rechnen.",
               priority=4, tags=["calendar"], dry_run=dry_run)
        state["warned"].append(key)
    state["warned"] = state["warned"][-200:]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true", help="nichts senden, Zustand nicht speichern")
    p.add_argument("--test-ai", action="store_true", help="die 5 neuesten Meldungen bewerten und nur anzeigen")
    args = p.parse_args()
    min_importance = int(os.environ.get("MIN_IMPORTANCE") or 4)

    if args.test_ai:
        epoch = dt.datetime.min.replace(tzinfo=dt.timezone.utc)
        newest = sorted(fetch_news(), key=lambda i: i["published"] or epoch, reverse=True)[:5]
        ratings = classify(newest)
        for i in newest:
            r = ratings.get(i["id"])
            print(f"{r['importance']} {r['direction']:8} {i['title']}\n  → {r['summary_de']}" if r
                  else f"? (nicht bewertet) {i['title']}")
        if len(ratings) != len(newest):
            sys.exit("Nicht alle Meldungen wurden bewertet.")
        return

    state = load_state()
    run_calendar(state, args.dry_run)
    run_news(state, args.dry_run, min_importance)
    if not args.dry_run:
        save_state(state)


if __name__ == "__main__":
    main()
