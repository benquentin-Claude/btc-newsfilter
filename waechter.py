#!/usr/bin/env python3
"""Wächter für einen getrennt laufenden Handels-Bot: schlägt Alarm, wenn sein Herzschlag ausbleibt.

Der Bot läuft auf einem eigenen Server und schreibt regelmäßig einen Herzschlag in einen geheimen ntfy-Kanal
(rund um die US-Session alle 10 Minuten, sonst stündlich). Dieses Skript läuft bei jedem News-Filter-Lauf mit
und liest nur diesen Kanal. Es kennt keine Zugangsdaten zum Handelskonto und kann nicht handeln.

Umgebungsvariablen:
  BOT_HEARTBEAT_TOPIC  geheimer Steuerkanal des Bots (GitHub-Secret). Fehlt er, tut das Skript nichts.
  NTFY_TOPIC           Kanal für den Alarm aufs Handy (GitHub-Secret)
  BOT_NAME             Name des Bots in den Meldungen (GitHub-Variable, Standard „Handels-Bot“)

Die Ausgabe ist öffentlich sichtbar. Sie nennt deshalb weder Kanäle noch Inhalte, nur das Ergebnis in einem Wort.
"""
import datetime as dt
import json
import os
import sys
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
BEAT, ALARM, CLEAR = "herzschlag", "waechter-alarm", "waechter-entwarnung"
SESSION_LIMIT = 35 * 60      # in der Session: Alarm, wenn so lange kein Herzschlag kam (der Bot sendet alle 10 Minuten)
IDLE_LIMIT = 3 * 3600        # sonst: der Bot sendet stündlich
SESSION_REPEAT = 60 * 60     # in der Session höchstens stündlich erinnern
IDLE_REPEAT = 12 * 3600      # sonst höchstens zweimal am Tag


def in_session(now):
    """Kurz vor bis kurz nach der US-Session (09:00–16:00 New York), montags bis freitags."""
    ny = now.astimezone(NY)
    return ny.weekday() < 5 and dt.time(8, 40) <= ny.time() <= dt.time(16, 20)


def decide(now_ts, messages, session):
    """Gibt (Entscheidung, Minuten seit dem letzten Herzschlag, Betriebsart) zurück.
    Entscheidung: „ok“, „alarm“, „still“ (Alarm läuft schon) oder „entwarnung“."""
    def last(title):
        return max((m["time"] for m in messages if m.get("title") == title), default=None)

    beats = sorted((m for m in messages if m.get("title") == BEAT), key=lambda m: m["time"])
    beat, alarm, clear = (beats[-1]["time"] if beats else None), last(ALARM), last(CLEAR)
    mode = str(beats[-1].get("message", "")).split(" ")[0] if beats else ""
    age = None if beat is None else (now_ts - beat) / 60
    if beat is None or now_ts - beat > (SESSION_LIMIT if session else IDLE_LIMIT):
        repeat = SESSION_REPEAT if session else IDLE_REPEAT
        return ("alarm" if alarm is None or now_ts - alarm >= repeat else "still"), age, mode
    if alarm is not None and beat > alarm and (clear is None or clear < alarm):
        return "entwarnung", age, mode
    return "ok", age, mode


def fetch(url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"} if data else {},
                                 method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read().decode()


def run(now, heartbeat_topic, alarm_topic, fetch=fetch, name="Handels-Bot"):
    raw = fetch(f"https://ntfy.sh/{urllib.parse.quote(heartbeat_topic)}/json?poll=1&since=12h")
    messages = []
    for line in raw.splitlines():
        try:
            m = json.loads(line)
        except ValueError:
            continue
        if m.get("event") == "message" and isinstance(m.get("time"), (int, float)):
            messages.append(m)
    session = in_session(now)
    verdict, age, mode = decide(now.timestamp(), messages, session)
    paper = mode == "papier"

    def send(topic, title, message="", priority=1):
        fetch("https://ntfy.sh/", {"topic": topic, "title": title, "message": message, "priority": priority})

    if verdict == "alarm":
        since = f"Seit {age:.0f} Minuten" if age is not None else "Seit über 12 Stunden"
        if session:
            text = (f"{since} kein Herzschlag vom Server. Bitte in der Handels-App nachsehen, ob eine Position "
                    "offen ist, und den Server prüfen (betrieb/server.sh status).")
            if paper:
                text = f"{since} kein Herzschlag vom Server (Papier-Betrieb, es ist kein Geld im Spiel). Bitte den Server prüfen."
        else:
            text = f"{since} kein Herzschlag vom Server. Bitte vor der nächsten Session prüfen (betrieb/server.sh status)."
        send(alarm_topic, f"{name} antwortet nicht", text, (4 if paper else 5) if session else 3)
        send(heartbeat_topic, ALARM)
    elif verdict == "entwarnung":
        send(alarm_topic, f"{name} ist wieder da", "Der Herzschlag kommt wieder.", 3)
        send(heartbeat_topic, CLEAR)
    return verdict


def main():
    heartbeat, alarm = os.environ.get("BOT_HEARTBEAT_TOPIC"), os.environ.get("NTFY_TOPIC")
    if not heartbeat or not alarm:
        print("Wächter: nicht eingerichtet")
        return 0
    try:
        print("Wächter:", run(dt.datetime.now(dt.timezone.utc), heartbeat, alarm, name=os.environ.get("BOT_NAME") or "Handels-Bot"))
    except Exception as exc:   # ntfy nicht erreichbar o. Ä.: nichts verraten, den News-Filter nicht aufhalten
        print("Wächter: Prüfung nicht möglich –", type(exc).__name__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
