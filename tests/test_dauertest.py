import json
import unittest
from types import SimpleNamespace
from unittest import mock

from . import leeren, text
from wifisurvey import anzeige
from wifisurvey import core as c
from wifisurvey import dauertest as d

AP1, AP2 = "02:00:00:00:00:01", "02:00:00:00:00:02"


def sekunden(n, **abweichend):
    """n ungestörte Sekunden; abweichend: {"s10_14": {feld: wert}} ändert die Sekunden 10 bis 14."""
    sek = [{"sek": i, "signal": -60, "bssid": AP1, "freq_mhz": 5260, "ping_gesendet": 5, "ping_verloren": 0,
            "rtt_max": 3.0, "down_verlust_pct": 0, "down_jitter_ms": 0.5, "down_pakete": 312,
            "up_verlust_pct": 0, "up_jitter_ms": 0.4, "up_pakete": 312} for i in range(n)]
    for bereich, werte in abweichend.items():
        a, _, b = bereich[1:].partition("_")
        for i in range(int(a), int(b or a) + 1):
            sek[i].update(werte)
    return sek


def bewertet(n, **abweichend):
    sek = sekunden(n, **abweichend)
    d.bewerten(sek)
    return sek, d.zusammenfassen(sek)


OHNE_VIDEO = {"down_pakete": 0, "down_verlust_pct": 0, "down_jitter_ms": 0}
NICHT_GEMESSEN = {"up_pakete": None, "up_verlust_pct": None, "up_jitter_ms": None}


class Ping(unittest.TestCase):
    def test_ausgabe_lesen(self):
        pings, info = {}, {}
        d._lies_ping(SimpleNamespace(stdout=text("ping.txt").splitlines()), 1794434190.0, pings, info)
        self.assertEqual(sorted(pings), list(range(1, 11)))
        self.assertEqual(info, {"gesendet": 10})
        zeit, rtt = pings[4]  # Empfang bei 1,079682 s abzüglich der Laufzeit
        self.assertAlmostEqual(zeit, 1.079642, places=5)
        self.assertEqual(rtt, 0.040)
        self.assertEqual({s: e[:2] for s, e in d._ping_sekunden(pings, 10).items()},
                         {0: [3, 0], 1: [5, 0], 2: [2, 0]})

    def test_uhr_statt_laufnummer(self):
        # ping sendet real alle ~208 ms: Nummer × 0,2 s läge nach 100 Pings schon 0,8 s daneben
        pings = {seq: ((seq - 1) * 0.208, 1.0) for seq in range(1, 101)}
        je_s = d._ping_sekunden(pings, 100)
        self.assertEqual(max(je_s), 20)
        self.assertEqual(sum(e[0] for e in je_s.values()), 100)
        self.assertEqual(sum(e[1] for e in je_s.values()), 0)

    def test_verlorene_zwischen_den_nachbarn(self):
        pings = {seq: ((seq - 1) * 0.25, 1.0) for seq in range(1, 21) if not 9 <= seq <= 12}
        je_s = d._ping_sekunden(pings, 20)
        self.assertEqual(je_s[2], [4, 4, []])
        self.assertEqual([je_s[s][1] for s in (0, 1, 3, 4)], [0, 0, 0, 0])

    def test_verlorene_am_ende(self):
        pings = {seq: ((seq - 1) * 0.25, 1.0) for seq in range(1, 11)}  # letzte Antwort bei 2,25 s
        je_s = d._ping_sekunden(pings, gesendet=14)
        self.assertEqual(je_s[2][:2], [5, 3])
        self.assertEqual(je_s[3][:2], [1, 1])

    def test_ueberfaellige_waehrend_des_tests(self):
        pings = {seq: ((seq - 1) * 0.25, 1.0) for seq in range(1, 6)}  # letzte Antwort bei 1,0 s
        je_s = d._ping_sekunden(pings, jetzt=4.1)  # bis 3,1 s müssten 10 weitere gesendet sein
        self.assertEqual(sum(e[0] for e in je_s.values()), 15)
        self.assertEqual(sum(e[1] for e in je_s.values()), 10)
        self.assertEqual(je_s[0][1], 0)

    def test_keine_antwort(self):
        self.assertEqual(d._ping_sekunden({}), {})
        je_s = d._ping_sekunden({}, jetzt=5.1)
        self.assertEqual(sum(e[1] for e in je_s.values()), 20)
        self.assertEqual(sum(len(e[2]) for e in je_s.values()), 0)


def intervall(start, pakete, verloren=0, dauer=1.0):
    return json.dumps({"event": "interval", "data": {"sum_bidir_reverse": {
        "start": start, "end": start + dauer, "seconds": dauer, "packets": pakete, "lost_packets": verloren,
        "lost_percent": 100 * verloren / pakete if pakete else 0, "jitter_ms": 0.5}}})


class Videostrom(unittest.TestCase):
    def lies(self, zeilen, jetzt=1061.0, t0=1000.0):
        down = {}
        zustand = {"offset": None, "null": 0, "zeile": 0, "server": "", "fehler": None}
        with mock.patch.object(d.time, "time", return_value=jetzt):
            d._lies_iperf(zeilen, t0, down, zustand)
        return down, zustand

    def test_aufzeichnung(self):
        # zweiter Abschnitt: das erste Intervall (1,0 s lang) trifft 61 s nach Testbeginn ein
        down, zustand = self.lies(text("iperf_stream.jsonl").splitlines())
        self.assertEqual(sorted(down), [60, 61, 62])
        self.assertEqual([down[s][2] for s in (60, 61, 62)], [313, 313, 312])
        self.assertEqual(down[60][0], 0)
        self.assertAlmostEqual(down[60][1], 0.0291, places=4)
        self.assertEqual((zustand["null"], zustand["fehler"]), (0, None))
        self.assertEqual(d._server_intervalle(zustand["server"]),  # ohne die Gesamtzeile 0.00-3.00
                         {0: (0.0, 0.032, 313), 1: (0.0, 0.020, 313), 2: (0.0, 0.026, 312)})

    def test_verlust(self):
        down, _ = self.lies([intervall(0, 312, 78)], jetzt=1001.0)
        self.assertEqual(down, {0: (25.0, 0.5, 234)})

    def test_restintervall_zaehlt_nicht(self):
        down, _ = self.lies([intervall(0, 312), intervall(1, 90, dauer=0.3)], jetzt=1001.0)
        self.assertEqual(sorted(down), [0])

    def test_stillstand_wird_gezaehlt(self):
        zeilen = [intervall(0, 312)] + [intervall(s, 0) for s in (1, 2, 3)]
        down, zustand = self.lies(zeilen, jetzt=1001.0)
        self.assertEqual(zustand["null"], 3)
        self.assertEqual(down[2], (0, 0.5, 0))
        _, zustand = self.lies(zeilen + [intervall(4, 312)], jetzt=1001.0)
        self.assertEqual(zustand["null"], 0)

    def test_fehlermeldungen(self):
        _, zustand = self.lies(['{"event":"error","data":"unable to connect to server"}'])
        self.assertEqual(zustand["fehler"], "iperf3: unable to connect to server")
        _, zustand = self.lies(["iperf3: error - control socket has closed unexpectedly\n"])
        self.assertEqual(zustand["fehler"], "iperf3: error - control socket has closed unexpectedly")

    def test_server_zeilen(self):
        zeile = "[  5][RX-S]   3.00-4.00   sec   183 KBytes  1.50 Mbits/sec  0.512 ms  156/312 (50%)  \n"
        rest = "[  5][RX-S]   4.00-4.25   sec   91 KBytes  3.00 Mbits/sec  0.100 ms  0/78 (0%)  \n"
        self.assertEqual(d._server_intervalle(zeile + rest), {3: (50.0, 0.512, 156)})
        self.assertEqual(d._server_intervalle(None), {})

    def test_fehlertext(self):
        self.assertEqual(d._fehlertext([], True, True), "")
        self.assertEqual(d._fehlertext([(125, "Videostrom lieferte keine Pakete mehr"), (140, "x")], True, False),
                         "Videostrom 2× neu gestartet (zuerst bei 2:05: Videostrom lieferte keine Pakete mehr); "
                         "keine Werte für die Senderichtung vom iperf3-Server")
        self.assertEqual(d._fehlertext([], False, False), "keine Werte vom Videostrom")


class Bewerten(unittest.TestCase):
    def test_ungestoert(self):
        sek, z = bewertet(60)
        self.assertEqual({s["gestoert"] for s in sek} | {s["messfehler"] for s in sek}, {""})
        self.assertEqual((z["aussetzer"], z["gestoerte_s"], z["messfehler_s"]), (0, 0, 0))
        self.assertEqual((z["video_down_s"], z["video_up_s"], z["ping_verlust_pct"]), (60, 60, 0))

    def test_abgerissener_videostrom_ist_kein_aussetzer(self):
        # der Fall aus der Praxis: 87 s lang kein Videopaket, aber jeder Ping kam an
        sek, z = bewertet(900, s0_899=NICHT_GEMESSEN, s700_786=OHNE_VIDEO)
        self.assertEqual((z["aussetzer"], z["gestoerte_s"]), (0, 0))
        self.assertEqual((z["messfehler_s"], z["video_down_s"], z["video_up_s"]), (87, 813, 0))
        self.assertEqual(sek[700]["messfehler"], d.MESSFEHLER)
        self.assertEqual(z["udp_down_verlust_pct"], 0)
        u = d.urteil({**z, "dauer_s": 900, "signal_avg": -60})
        self.assertEqual((u["stufe"], u["gruende"], u["basis"]), ("gut", [], "Dauertest, Video ↑ nicht gemessen"))

    def test_echter_aussetzer(self):
        sek, z = bewertet(900, s700_704={**OHNE_VIDEO, "ping_verloren": 5, "rtt_max": None})
        self.assertEqual(sek[700]["gestoert"], "Ping, Video ↓")
        self.assertEqual(sek[700]["messfehler"], "")
        self.assertEqual((z["aussetzer"], z["laengster_aussetzer_s"], z["gestoerte_s"], z["messfehler_s"]),
                         (1, 5, 5, 0))
        self.assertEqual(d.urteil({**z, "dauer_s": 900})["stufe"], "schlecht")

    def test_teilverlust_bei_gutem_ping_bleibt_gestoert(self):
        sek, z = bewertet(60, s10={"down_verlust_pct": 20, "down_pakete": 250}, s20={"up_pakete": 200})
        self.assertEqual((sek[10]["gestoert"], sek[20]["gestoert"]), ("Video ↓", "Video ↑"))
        self.assertEqual((z["aussetzer"], z["messfehler_s"]), (2, 0))

    def test_an_und_auslaufen_gehoert_zum_abriss(self):
        halb = {"down_pakete": 100}
        sek, z = bewertet(60, s9=halb, s10_12=OHNE_VIDEO, s13=halb)
        self.assertEqual([bool(s["messfehler"]) for s in sek[8:15]], [False, True, True, True, True, True, False])
        self.assertEqual((z["aussetzer"], z["messfehler_s"]), (0, 5))

    def test_abriss_dauert_an_bis_der_ping_ausfaellt(self):
        sek, z = bewertet(60, s10_12=OHNE_VIDEO, s11={"ping_gesendet": 2}, s12={"ping_verloren": 3})
        self.assertEqual([s["messfehler"] for s in sek[10:13]], [d.MESSFEHLER, d.MESSFEHLER, ""])
        self.assertEqual(sek[12]["gestoert"], "Ping, Video ↓")
        self.assertEqual(z["aussetzer"], 1)

    def test_stillstand_ohne_ping_beleg_ist_ein_aussetzer(self):
        # ohne einen vollständigen Ping in derselben Sekunde gibt es keinen Beweis für ein intaktes WLAN
        sek, z = bewertet(60, s10_12={**OHNE_VIDEO, "ping_gesendet": 2})
        self.assertEqual((z["aussetzer"], z["laengster_aussetzer_s"], z["messfehler_s"]), (1, 3, 0))

    def test_pause_zwischen_abschnitten(self):
        luecke = {**NICHT_GEMESSEN, "down_pakete": None, "down_verlust_pct": None, "down_jitter_ms": None}
        sek, z = bewertet(120, s60_61=luecke)
        self.assertEqual((z["aussetzer"], z["messfehler_s"], z["video_down_s"], z["video_up_s"]), (0, 0, 118, 118))

    def test_ping_kriterien(self):
        sek, z = bewertet(60, s5={"ping_verloren": 1}, s6={"ping_verloren": 2}, s7={"rtt_max": 301.0},
                          s20={"bssid": None, "signal": None})
        self.assertEqual([s["gestoert"] for s in sek[5:8]], ["", "Ping", "RTT"])
        self.assertEqual(sek[20]["gestoert"], "keine Verbindung")
        self.assertEqual((z["aussetzer"], z["laengster_aussetzer_s"], z["ohne_verbindung_s"]), (2, 2, 1))
        self.assertEqual(z["ping_verlust_pct"], 1.0)  # 3 von 300
        self.assertEqual(z["rtt_max"], 301.0)

    def test_wechsel(self):
        _, z = bewertet(60, s20_39={"bssid": AP2, "freq_mhz": 5500}, s50_59={"freq_mhz": 5180})
        self.assertEqual((z["roams"], z["kanalwechsel"]), (2, 1))

    def test_verlust_nach_paketen_gewichtet(self):
        _, z = bewertet(10, s3={"down_verlust_pct": 50, "down_pakete": 156})
        self.assertEqual(z["udp_down_verlust_pct"], 5.0)  # 156 von 3120 Paketen
        self.assertEqual(z["udp_up_verlust_pct"], 0)

    def test_auswerten_fuehrt_alles_zusammen(self):
        pings = {seq: ((seq - 1) * 0.2 + 0.01, 2.0 + seq % 3) for seq in range(1, 51)}  # 10 s, alle beantwortet
        werte = {s: (0, 0.5, 312) for s in range(10)}
        erg = d._auswerten([(-60, AP1, 5260)] * 10, pings, {**werte, 4: (40.0, 2.0, 187)}, werte, 10,
                           {"gesendet": 50}, True)
        z = erg["zusammenfassung"]
        self.assertEqual([s["gestoert"] for s in erg["sekunden"]], [""] * 4 + ["Video ↓"] + [""] * 5)
        self.assertEqual((z["aussetzer"], z["ping_verlust_pct"], z["rtt_max"], z["rtt_p99"]), (1, 0, 4.0, 4.0))
        self.assertEqual(z["rtt_avg"], 3.0)


class Urteil(unittest.TestCase):
    BASIS = {"dauer_s": 900, "aussetzer": 0, "udp_up_verlust_pct": 0.1, "udp_down_verlust_pct": 0,
             "udp_up_jitter_ms": 1, "udp_down_jitter_ms": 1, "ping_verlust_pct": 0, "signal_avg": -70,
             "video_up_s": 880, "video_down_s": 895}

    def urteil(self, **kw):
        return d.urteil({**self.BASIS, **kw})

    def test_gut(self):
        self.assertEqual(self.urteil(), {"stufe": "gut", "punkte": 2, "gruende": [], "basis": "Dauertest",
                                         "reserve_db": 12})

    def test_aussetzer(self):
        u = self.urteil(aussetzer=1, laengster_aussetzer_s=2)
        self.assertEqual((u["stufe"], u["gruende"]), ("geht", ["1 Aussetzer (≈0.7 je 10 min), längster 2 s"]))
        self.assertEqual(self.urteil(aussetzer=1, laengster_aussetzer_s=5)["stufe"], "schlecht")
        self.assertEqual(self.urteil(aussetzer=5, laengster_aussetzer_s=1)["stufe"], "schlecht")  # 3,3 je 10 min
        self.assertEqual(self.urteil(ohne_verbindung_s=2)["gruende"], ["2 s ohne Verbindung"])

    def test_verlust_und_jitter(self):
        self.assertEqual(self.urteil(udp_down_verlust_pct=0.6)["stufe"], "geht")
        self.assertEqual(self.urteil(udp_down_verlust_pct=2)["stufe"], "schlecht")
        self.assertEqual(self.urteil(udp_up_jitter_ms=4)["gruende"], ["Jitter 4 ms"])
        self.assertEqual(self.urteil(ping_verlust_pct=1)["stufe"], "gut")  # mit Videostrom zählt dessen Verlust

    def test_wechsel(self):
        u = self.urteil(roams=1, kanalwechsel=2)
        self.assertEqual((u["stufe"], u["gruende"]), ("geht", ["1× AP-Wechsel", "2× Kanalwechsel"]))

    def test_nur_ping(self):
        nur_ping = {"dauer_s": 900, "aussetzer": 0, "ping_verlust_pct": 0.6, "udp_up_verlust_pct": "",
                    "udp_down_verlust_pct": ""}
        u = d.urteil(nur_ping)
        self.assertEqual((u["stufe"], u["gruende"], u["basis"]), ("geht", ["Verlust 0.6 %"], "Dauertest (nur Ping)"))

    def test_abdeckung_steht_in_der_basis(self):
        self.assertEqual(self.urteil(video_down_s=720)["basis"], "Dauertest, Video ↓ nur 12:00 von 15:00 gemessen")
        self.assertEqual(self.urteil(video_up_s=0)["basis"], "Dauertest, Video ↑ nicht gemessen")
        self.assertEqual(self.urteil(video_up_s="", video_down_s="")["basis"], "Dauertest")  # alter Test ohne Angabe

    def test_anzeige(self):
        r = {**self.BASIS, "messfehler_s": 87, "ap": "Fritzbox", "freq_mhz": 5260,
             "iperf_fehler": "Videostrom 1× neu gestartet"}
        zeilen = {z[0]: z[1:] for z in anzeige.dauertest_zeilen(r)}
        self.assertEqual(zeilen["Videocall"], ("● gut · Reserve 12 dB  (Basis: Dauertest)", "gut"))
        self.assertEqual(zeilen["Aussetzer"], ("0", "gut"))
        self.assertIn("87 s Videostrom ohne Daten bei intaktem Ping", zeilen["Messfehler"][0])
        self.assertEqual(zeilen["⚠ iperf3"], ("Videostrom 1× neu gestartet", "mittel"))
        self.assertNotIn("Messfehler", {z[0] for z in anzeige.dauertest_zeilen(self.BASIS)})


class AlteDaten(unittest.TestCase):
    """Tests, die vor der Gegenprobe per Ping gespeichert wurden, werden einmalig neu bewertet."""

    def setUp(self):
        leeren()
        alt_felder, alt_sek_felder = d.DAUER_FELDER[:-5], d.SEK_FELDER[:-1]
        self.assertNotIn("bewertung", alt_felder)
        self.assertNotIn("messfehler", alt_sek_felder)
        sek = sekunden(900, s0_899=NICHT_GEMESSEN, s700_786=OHNE_VIDEO)
        for s in sek[700:787]:
            s["gestoert"] = "Video ↓"  # so stand es nach den alten Regeln in der Datei
        c.append_csv(d.SEKUNDEN, alt_sek_felder, [{**s, "id": "D1"} for s in sek])
        c.append_csv(d.DAUERTESTS, alt_felder, [
            {"id": "D1", "szenario": "Decke", "ort": "Esstisch", "dauer_s": 900, "signal_avg": -60,
             "udp_down_verlust_pct": 9.67, "udp_down_jitter_ms": 0.5, "ping_verlust_pct": 0,
             "gestoerte_s": 87, "aussetzer": 1, "laengster_aussetzer_s": 87, "roams": 0, "kanalwechsel": 0,
             "ohne_verbindung_s": 0, "urteil": "schlecht", "gruende": "1 Aussetzer"},
            {"id": "D0", "szenario": "Decke", "ort": "Sofa", "dauer_s": 600, "signal_avg": -55,  # ohne Sekundenwerte
             "ping_verlust_pct": 0, "aussetzer": 0, "urteil": "gut"}])

    def test_neu_bewerten(self):
        tests = {t["id"]: t for t in d.laden()}
        t = tests["D1"]
        self.assertEqual((t["urteil"], t["gruende"]), ("gut", ""))
        self.assertEqual([c.num(t[k]) for k in ("aussetzer", "gestoerte_s", "messfehler_s", "video_down_s")],
                         [0, 0, 87, 813])
        self.assertEqual(c.num(t["udp_down_verlust_pct"]), 0)
        self.assertEqual(c.num(t["signal_avg"]), -60)  # Messwerte bleiben
        self.assertEqual(tests["D0"]["urteil"], "gut")

        gespeichert = {t["id"]: t for t in c.read_csv(d.DAUERTESTS)}
        self.assertEqual((gespeichert["D1"]["urteil"], gespeichert["D1"]["bewertung"]), ("gut", d.BEWERTUNG))
        self.assertEqual(gespeichert["D0"]["bewertung"], d.BEWERTUNG)
        sek = c.read_csv(d.SEKUNDEN)
        self.assertEqual(len(sek), 900)
        self.assertEqual((sek[700]["gestoert"], sek[700]["messfehler"], sek[700]["down_pakete"]),
                         ("", d.MESSFEHLER, "0"))
        alt = c.read_csv(d.DAUERTESTS.with_suffix(".csv.bak"))
        self.assertEqual([(t["id"], t["urteil"]) for t in alt], [("D1", "schlecht"), ("D0", "gut")])

    def test_nur_einmal(self):
        d.laden()
        with mock.patch.object(d, "_neu_bewerten") as neu:
            self.assertEqual(len(d.laden()), 2)
            self.assertEqual([t["id"] for t in d.laden("Decke")], ["D1", "D0"])
            self.assertEqual(d.laden("Anderes"), [])
        neu.assert_not_called()

    def test_speichern_und_loeschen(self):
        sek, z = bewertet(60)
        d.speichern({"id": "D2", "szenario": "Decke", "ort": "Sofa", "dauer_s": 60, **z, "bewertung": d.BEWERTUNG},
                    [{**s, "id": "D2"} for s in sek])
        self.assertEqual([t["id"] for t in d.laden()], ["D1", "D0", "D2"])
        d.loeschen("D1")
        self.assertEqual([t["id"] for t in d.laden()], ["D0", "D2"])
        self.assertEqual({s["id"] for s in c.read_csv(d.SEKUNDEN)}, {"D2"})


if __name__ == "__main__":
    unittest.main()
