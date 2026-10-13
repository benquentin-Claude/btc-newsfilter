# BTC-Newsfilter

Läuft alle 10 Minuten in GitHub Actions, auch wenn der Mac aus ist.

1. Liest Krypto-News per RSS von CoinDesk, Cointelegraph, The Block, Decrypt und Bitcoin Magazine.
2. Lässt neue Meldungen von Claude bewerten: Wichtigkeit von 1 bis 5, bullish, bearish oder neutral, dazu ein deutscher Satz als Zusammenfassung.
3. Schickt Meldungen ab der eingestellten Wichtigkeit (Standard: 4) per [ntfy](https://ntfy.sh) aufs Handy und an den Mac.
4. Warnt 30 Minuten vor US-Wirtschaftsterminen mit hohem Einfluss, zum Beispiel CPI, FOMC oder NFP. Die Daten kommen aus dem Forex-Factory-Kalender.

`state.json` merkt sich, welche Meldungen schon bewertet wurden. Der Workflow schreibt die Datei nach jedem Lauf zurück ins Repo.

## Wächter für den Handels-Bot

Bei jedem Lauf prüft `waechter.py` zusätzlich, ob ein getrennt laufender Handels-Bot noch lebt. Der Bot schreibt regelmäßig einen Herzschlag in einen eigenen ntfy-Kanal. Bleibt der Herzschlag aus, geht ein Alarm aufs Handy, in der US-Handelszeit nach spätestens 35 Minuten ohne Signal, sonst nach 3 Stunden. Kommt er zurück, folgt eine Entwarnung. Der Wächter liest nur diesen Kanal, kennt keine Zugangsdaten zum Handelskonto und kann nicht handeln. Ohne das Secret `BOT_HEARTBEAT_TOPIC` tut er nichts.

    python3 -m unittest test_waechter.py

## Einstellungen (GitHub → Settings → Secrets and variables → Actions)

| Name | Art | Bedeutung |
|---|---|---|
| `ANTHROPIC_API_KEY` | Secret | API-Schlüssel von console.anthropic.com |
| `NTFY_TOPIC` | Secret | geheimer Kanalname bei ntfy (wer ihn kennt, kann mitlesen) |
| `BOT_HEARTBEAT_TOPIC` | Secret | geheimer ntfy-Kanal mit dem Herzschlag des Handels-Bots (optional) |
| `MIN_IMPORTANCE` | Variable | ab welcher Wichtigkeit gemeldet wird (1–5, Standard 4) |
| `CLAUDE_MODEL` | Variable | Modell für die Bewertung (Standard `claude-haiku-5-5`, genauer: `claude-sonnet-5-5` oder `claude-opus-5-5`) |

## Lokal testen

    python3 -m venv .venv && .venv/bin/pip install feedparser
    .venv/bin/python newsfilter.py --dry-run

Mit `--dry-run` wird nichts gesendet und nichts gespeichert. Ohne `ANTHROPIC_API_KEY` listet das Skript neue Meldungen nur auf und bewertet sie nicht.
