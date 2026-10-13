"""Tests für den Wächter:  python3 -m unittest test_waechter.py"""
import datetime as dt
import json
import unittest

import waechter as w

T = dt.datetime(2026, 10, 6, 12, 0, tzinfo=w.NY)   # Dienstag mitten in der Session
NOW = T.timestamp()


def beat(minutes_ago, mode="demo"):
    return {"event": "message", "time": NOW - 60 * minutes_ago, "title": w.BEAT, "message": f"{mode} ok"}


def mark(title, minutes_ago):
    return {"event": "message", "time": NOW - 60 * minutes_ago, "title": title, "message": ""}


class Net:
    """Nachgebautes ntfy: liefert die Nachrichten des Steuerkanals und sammelt, was gesendet wird."""

    def __init__(self, messages):
        self.messages, self.sent = messages, []

    def __call__(self, url, body=None):
        if body is not None:
            self.sent.append(body)
            return ""
        return "\n".join(json.dumps(m) for m in self.messages) + "\nkein json\n"


class Waechter(unittest.TestCase):
    def test_sessionfenster(self):
        self.assertTrue(w.in_session(T))
        self.assertFalse(w.in_session(dt.datetime(2026, 10, 6, 8, 30, tzinfo=w.NY)))
        self.assertFalse(w.in_session(dt.datetime(2026, 10, 6, 17, 0, tzinfo=w.NY)))
        self.assertFalse(w.in_session(dt.datetime(2026, 10, 10, 12, 0, tzinfo=w.NY)))   # Samstag

    def test_entscheidung(self):
        self.assertEqual(w.decide(NOW, [beat(5)], True)[0], "ok")
        self.assertEqual(w.decide(NOW, [beat(40)], True)[0], "alarm")
        self.assertEqual(w.decide(NOW, [beat(40)], False)[0], "ok")              # außerhalb der Session reicht stündlich
        self.assertEqual(w.decide(NOW, [beat(200)], False)[0], "alarm")
        self.assertEqual(w.decide(NOW, [], True)[0], "alarm")                     # gar kein Herzschlag in 12 Stunden
        self.assertEqual(w.decide(NOW, [beat(90), mark(w.ALARM, 30)], True)[0], "still")
        self.assertEqual(w.decide(NOW, [beat(120), mark(w.ALARM, 61)], True)[0], "alarm")   # stündlich erinnern
        self.assertEqual(w.decide(NOW, [beat(300), mark(w.ALARM, 90)], False)[0], "still")
        self.assertEqual(w.decide(NOW, [mark(w.ALARM, 30), beat(5)], True)[0], "entwarnung")
        self.assertEqual(w.decide(NOW, [mark(w.ALARM, 30), beat(5), mark(w.CLEAR, 2)], True)[0], "ok")

    def test_alarm_geht_aufs_handy_und_wird_vermerkt(self):
        net = Net([beat(50)])
        self.assertEqual(w.run(T, "steuer", "handy", net), "alarm")
        self.assertEqual([(m["topic"], m["title"], m["priority"]) for m in net.sent],
                         [("handy", "Handels-Bot antwortet nicht", 5), ("steuer", w.ALARM, 1)])
        self.assertIn("50 Minuten", net.sent[0]["message"])

    def test_papier_betrieb_ist_weniger_dringend(self):
        net = Net([beat(50, "papier")])
        w.run(T, "steuer", "handy", net)
        self.assertEqual(net.sent[0]["priority"], 4)
        self.assertIn("kein Geld im Spiel", net.sent[0]["message"])

    def test_ruhe_solange_alles_laeuft_und_entwarnung_nur_einmal(self):
        net = Net([beat(3)])
        self.assertEqual(w.run(T, "steuer", "handy", net), "ok")
        self.assertEqual(net.sent, [])
        net = Net([mark(w.ALARM, 30), beat(3)])
        self.assertEqual(w.run(T, "steuer", "handy", net, name="Mein Bot"), "entwarnung")
        self.assertEqual([m["title"] for m in net.sent], ["Mein Bot ist wieder da", w.CLEAR])

    def test_ohne_einrichtung_passiert_nichts(self):
        self.assertEqual(w.main(), 0)


if __name__ == "__main__":
    unittest.main()
