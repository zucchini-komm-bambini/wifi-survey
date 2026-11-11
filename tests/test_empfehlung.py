import contextlib
import io
import unittest

from . import leeren
from wifisurvey import cli
from wifisurvey import core as c
from wifisurvey import dauertest
from wifisurvey import empfehlung as e

FRITZ, DECKE = "02:00:00:00:00:01", "02:00:00:00:09:01"
GUT = {"udp_up_verlust_pct": 0, "udp_down_verlust_pct": 0, "udp_up_jitter_ms": 1, "udp_down_jitter_ms": 1,
       "ping_verlust_pct": 0, "rtt_avg": 5, "rtt_max": 40, "iperf_up_mbit": 80, "iperf_down_mbit": 200,
       "signal_avg": -60, "ssid": "Heimnetz"}


def zelle(stufe, reserve=20, ap="AP"):
    return {"stufe": stufe, "punkte": c.STUFEN.index(stufe), "reserve": reserve, "aps": [ap], "knapp": False}


def pro_sz(orte, szen, zellen):
    return {s: {"gut": sum(1 for o in orte if zellen.get((o, s), {}).get("stufe") == "gut")} for s in szen}


class Kombinationen(unittest.TestCase):
    ORTE = ["Tisch", "Sofa", "Bett", "Balkon"]
    SZEN = ["Fritzbox", "Schrank", "Decke"]
    ZELLEN = {
        ("Tisch", "Fritzbox"): zelle("gut", ap="Fritzbox"), ("Sofa", "Fritzbox"): zelle("schlecht", ap="Repeater"),
        ("Bett", "Fritzbox"): zelle("gut", ap="Repeater"),
        ("Tisch", "Schrank"): zelle("geht"), ("Sofa", "Schrank"): zelle("gut"), ("Bett", "Schrank"): zelle("gut", 3),
        ("Tisch", "Decke"): zelle("gut", 25), ("Sofa", "Decke"): zelle("geht"), ("Bett", "Decke"): zelle("gut", 10),
    }

    def kombis(self):
        return e.kombinationen(self.ORTE, self.SZEN, self.ZELLEN, pro_sz(self.ORTE, self.SZEN, self.ZELLEN))

    def test_nur_paare_die_mehr_orte_gut_abdecken(self):
        k = self.kombis()
        # Fritzbox + Decke bringt nichts: beide sind an Tisch und Bett gut, keins am Sofa
        self.assertEqual([x["setups"] for x in k], [("Fritzbox", "Schrank"), ("Schrank", "Decke")])
        self.assertEqual([(x["gut"], x["geht"], x["schlecht"]) for x in k], [(3, 0, 0), (3, 0, 0)])
        self.assertEqual(k[0]["fehlend"], ["Balkon"])
        self.assertEqual(k[0]["schwach"], [])

    def test_je_ort_das_bessere_setup(self):
        k = self.kombis()
        # Bett ist in beiden gut: es zählt das Setup mit mehr Signalreserve
        self.assertEqual(k[0]["versorgt"], {"Fritzbox": {"Fritzbox": ["Tisch"], "Repeater": ["Bett"]},
                                            "Schrank": {"AP": ["Sofa"]}})
        self.assertEqual(k[1]["versorgt"], {"Schrank": {"AP": ["Sofa"]}, "Decke": {"AP": ["Tisch", "Bett"]}})

    def test_text(self):
        self.assertEqual(e.kombination_zeilen(self.kombis()[0]),
                         ["Fritzbox über Fritzbox: Tisch", "Fritzbox über Repeater: Bett", "Schrank: Sofa",
                          "in beiden nicht gemessen: Balkon"])

    def test_schwache_orte_bleiben_sichtbar(self):
        zellen = {**self.ZELLEN, ("Balkon", "Fritzbox"): zelle("schlecht"), ("Balkon", "Schrank"): zelle("geht")}
        k = e.kombinationen(self.ORTE, self.SZEN, zellen, pro_sz(self.ORTE, self.SZEN, zellen))[0]
        self.assertEqual((k["gut"], k["geht"], k["fehlend"], k["schwach"]), (3, 1, [], [("Balkon", "geht")]))
        self.assertEqual(e.kombination_zeilen(k)[-1], "bleibt schwach: Balkon (geht)")


class Gesamtbild(unittest.TestCase):
    def setUp(self):
        leeren()
        c.speichere_orte(["Tisch", "Sofa", "Bett"])
        c.szenario_speichern("Fritzbox", "Router im Flur")
        c.szenario_speichern("Decke")
        c.APS.write_text("02:00:00:00:00 = Fritzbox\n02:00:00:00:09 = cAP\n")
        m = lambda i, sz, ort, bssid=FRITZ, **kw: {**GUT, "id": f"M{i}", "szenario": sz, "ort": ort,  # noqa: E731
                                                  "bssid": bssid, **kw}
        c.append_csv(c.MESSUNGEN, c.MESS_FELDER, [
            m(1, "Fritzbox", "Tisch"),
            m(2, "Fritzbox", "Tisch", udp_up_verlust_pct=1.2),   # dieselbe Stelle, einmal schlechter
            m(3, "Fritzbox", "Sofa", signal_avg=-78),            # gut, aber nur 4 dB Reserve
            m(4, "Fritzbox", "Bett", udp_down_verlust_pct=5),
            m(5, "Decke", "Tisch", DECKE),
            m(6, "Decke", "Bett", DECKE),
            m(7, "Decke", "Sofa", DECKE),
        ])
        dauertest.speichern({"id": "D1", "szenario": "Decke", "ort": "Sofa", "bssid": DECKE, "dauer_s": 900,
                             "aussetzer": 2, "laengster_aussetzer_s": 8, "udp_up_verlust_pct": 0,
                             "udp_down_verlust_pct": 0, "signal_avg": -58, "bewertung": dauertest.BEWERTUNG}, [])
        self.d = e.empfehlung_daten()

    def test_schlechtestes_urteil_je_zelle(self):
        z = self.d["zellen"][("Tisch", "Fritzbox")]
        self.assertEqual((z["stufe"], z["n"], z["schwankt"], z["gruende"]), ("geht", 2, True, ["Verlust 1.2 %"]))
        self.assertEqual(z["aps"], ["Fritzbox"])

    def test_dauertest_zaehlt_mit(self):
        z = self.d["zellen"][("Sofa", "Decke")]
        self.assertEqual((z["stufe"], z["dauertest"], z["n"], z["reserve"]), ("schlecht", "schlecht", 2, 22))

    def test_knapp(self):
        z = self.d["zellen"][("Sofa", "Fritzbox")]
        self.assertEqual((z["stufe"], z["knapp"], z["reserve"]), ("gut", True, 4))
        self.assertFalse(self.d["zellen"][("Tisch", "Decke")]["knapp"])
        self.assertIn("Gut, aber knapp (unter 6 dB Signalreserve, kann an einem schlechten Tag kippen): "
                      "Sofa mit Fritzbox (4 dB).", self.d["fazit"])

    def test_rangfolge_und_orte(self):
        self.assertEqual(self.d["rangfolge"], ["Decke", "Fritzbox"])  # 2 von 3 gut gegen 1 von 3
        self.assertEqual(self.d["pro_sz"]["Fritzbox"]["schwach"], [("Tisch", "geht"), ("Bett", "schlecht")])
        self.assertEqual(self.d["pro_ort"]["Sofa"], {"stufe": "gut", "beste": ["Fritzbox"]})
        self.assertEqual(self.d["fazit"][0], "Bestes Setup: „Decke“ – gut an 2 von 3 gemessenen Orten.")

    def test_kombination(self):
        k = self.d["kombinationen"]
        self.assertEqual([(x["setups"], x["gut"]) for x in k], [(("Fritzbox", "Decke"), 3)])
        self.assertEqual(k[0]["versorgt"], {"Fritzbox": {"Fritzbox": ["Sofa"]}, "Decke": {"cAP": ["Tisch", "Bett"]}})
        self.assertEqual(self.d["fazit"][-1],
                         "Beste Kombination: „Fritzbox“ + „Decke“ – gut an 3 von 3 Orten („Decke“ allein: 2).")

    def test_ausgabe(self):
        aus = io.StringIO()
        with contextlib.redirect_stdout(aus):
            cli.main(["empfehlung"])
        t = aus.getvalue()
        for teil in ("Kombinationen", "Fritzbox + Decke", "wenig Signalreserve", "Beste Kombination"):
            self.assertIn(teil, t)


if __name__ == "__main__":
    unittest.main()
