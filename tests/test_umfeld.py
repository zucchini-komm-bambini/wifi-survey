import unittest

from . import leeren
from wifisurvey import ansage
from wifisurvey import umfeld as u


class Frequenzbereich(unittest.TestCase):
    def test_5ghz_bloecke(self):
        self.assertEqual(u.bereich(52, 5260, 80), (5250, 5330))
        self.assertEqual(u.bereich(None, 5680, 80), (5650, 5730))  # Kanal 136 liegt im Block ab 132
        self.assertEqual(u.bereich(64, None, 40), (5290, 5330))
        self.assertEqual(u.bereich(36, None, 160), (5170, 5330))
        self.assertEqual(u.bereich(100, None, 20), (5490, 5510))

    def test_2_4ghz(self):
        self.assertEqual(u.bereich(1, None, 20), (2402, 2422))
        self.assertEqual(u.bereich(3, None, 40), (2412, 2452))    # Erweiterung nach oben
        self.assertEqual(u.bereich(11, None, 40), (2432, 2472))   # Erweiterung nach unten

    def test_bekannte_kanalmitte(self):
        self.assertEqual(u.bereich(None, 5260, 80, mitte=5290), (5250, 5330))
        self.assertIsNone(u.bereich(None, None, 20))

    def test_ueberlappung(self):
        self.assertTrue(u.ueberlappt((5250, 5330), (5310, 5330)))
        self.assertFalse(u.ueberlappt((5250, 5330), (5170, 5250)))  # Nachbarblöcke berühren sich nur
        self.assertFalse(u.ueberlappt((5250, 5330), None))

    def test_kanal_aus_frequenz(self):
        self.assertEqual([u._kanal_aus_freq(f) for f in (2412, 2484, 5260, 5955)], [1, 14, 52, 1])
        self.assertEqual([u.kanal_freq(k, b) for k, b in ((6, "2.4"), (14, "2.4"), (100, "5"), (5, "6"))],
                         [2437, 2484, 5500, 5975])


class Funkumfeld(unittest.TestCase):
    def setUp(self):
        leeren()
        self.r = {"id": "M1", "ssid": "Heimnetz", "bssid": "02:00:00:00:00:01", "freq_mhz": 5260,
                  "kanalbreite_mhz": 80, "kanalmitte_mhz": 5290}
        n = lambda bssid, ssid, kanal, breite, dbm, verbunden="": {  # noqa: E731
            "bssid": bssid, "ssid": ssid, "kanal": kanal, "freq_mhz": 5000 + 5 * kanal, "bandbreite_mhz": breite,
            "signal_dbm": dbm, "verbunden": verbunden}
        self.netze = [
            n("02:00:00:00:00:01", "Heimnetz", 52, 80, -60, "ja"),
            n("12:00:00:00:00:07", "Cafe: Gast", 60, 20, -70),      # liegt im eigenen 80-MHz-Block
            n("12:00:00:00:00:08", "", 52, 80, -65),
            n("12:00:00:00:00:09", "Weit weg", 52, 80, -90),        # zu schwach, um den Kanal zu belegen
            n("12:00:00:00:00:0a", "Nebenan", 36, 80, -50),         # anderer Block
            n("02:00:00:00:01:01", "Heimnetz", 56, 20, -72),        # eigener Repeater auf demselben Kanal
            n("06:00:00:00:01:01", "Heimnetz-Gast", 56, 20, -72),   # dessen Gastnetz: dasselbe Gerät
        ]

    def test_lage(self):
        lage = u.lage_der_messung(self.r, self.netze)
        self.assertEqual(lage["bereich"], (5250, 5330))
        self.assertEqual([n["ssid"] for n in lage["fremde"]], ["", "Cafe: Gast"])  # stärkstes zuerst
        self.assertEqual([n["ssid"] for n in lage["eigene"]], ["Heimnetz", "Heimnetz-Gast"])
        self.assertEqual(u.lage_text(lage),
                         "2 fremde Netze überlappen (stärkstes: (verborgen) -65 dBm), 2 eigene auf demselben Kanal")

    def test_ruhiger_kanal(self):
        lage = u.lage_der_messung(self.r, self.netze[:1] + self.netze[3:5])
        self.assertEqual(u.lage_text(lage), "keine fremden Netze auf dem Kanal")

    def test_ohne_scan(self):
        self.assertIsNone(u.lage_der_messung(self.r))
        self.assertIsNone(u.lage_text(None))


class Ansage(unittest.TestCase):
    def test_restzeit(self):
        self.assertEqual(ansage.restzeit_text(23), "noch 25 Sekunden")
        self.assertEqual(ansage.restzeit_text(7), "noch 7 Sekunden")
        self.assertEqual(ansage.restzeit_text(600, 60), "noch 10 Minuten")
        self.assertEqual(ansage.restzeit_text(95, 60), "noch 2 Minuten")
        self.assertEqual(ansage.restzeit_text(61, 60), "noch eine Minute")
        self.assertEqual(ansage.restzeit_text(30, 60), "noch 30 Sekunden")


if __name__ == "__main__":
    unittest.main()
