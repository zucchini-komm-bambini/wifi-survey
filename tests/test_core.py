import json
import unittest
from unittest import mock

from . import leeren, text
from wifisurvey import anzeige
from wifisurvey import core as c

AP1, AP2, AP3 = "02:00:00:00:00:01", "02:00:00:00:00:02", "02:00:00:00:01:01"


class Auslesen(unittest.TestCase):
    def test_station(self):
        with mock.patch.object(c, "run", return_value=text("station.txt")):
            st = c.station("wlan0")
        self.assertEqual(st, {"bssid": AP1, "signal": -62, "signal_avg": -62, "beacon_signal_avg": -61,
                              "tx_bitrate": 650, "rx_bitrate": 780, "tx_packets": 6703, "tx_retries": 2003,
                              "tx_failed": 0, "beacon_loss": 0, "rx_drop_misc": 25})

    def test_station_ohne_verbindung(self):
        with mock.patch.object(c, "run", return_value=""):
            self.assertIsNone(c.station("wlan0"))

    def test_link(self):
        with mock.patch.object(c, "run", return_value=text("link.txt")):
            self.assertEqual(c.link("wlan0"), ("Heimnetz", 5260.0))
        with mock.patch.object(c, "run", return_value="Not connected.\n"):
            self.assertEqual(c.link("wlan0"), (None, None))

    def _scan(self, alle=None):
        def run(cmd, timeout=30):
            return text("nmcli.txt") if cmd[0] == "nmcli" else text("scandump.txt")
        with mock.patch.object(c, "run", side_effect=run):
            return c.scan("wlan0", "Heimnetz", namen={"02:00:00:00:00": "Fritzbox"}, verbunden=AP1.upper(), alle=alle)

    def test_scan_eigene_aps(self):
        aps = self._scan()
        self.assertEqual([a["bssid"] for a in aps], [AP2, AP1, AP3])  # stärkster zuerst
        # nmcli-Prozent -> dBm; für den verbundenen AP gilt der echte Wert aus iw
        self.assertEqual([a["signal_dbm"] for a in aps], [-53, -60, -76])
        self.assertEqual([a["quelle"] for a in aps], ["nmcli≈", "iw", "nmcli≈"])
        self.assertEqual([a["ap"] for a in aps], ["Fritzbox", "Fritzbox", AP3])  # ohne Namen: die BSSID
        self.assertEqual([a["verbunden"] for a in aps], ["", "ja", ""])
        self.assertEqual([a["band"] for a in aps], ["2.4", "5", "2.4"])
        self.assertEqual((aps[1]["kanal"], aps[1]["bandbreite_mhz"], aps[1]["freq_mhz"]), (52, 80, 5260))

    def test_scan_alle_netze(self):
        alle = []
        self._scan(alle)
        self.assertEqual(len(alle), 5)
        cafe = next(n for n in alle if n["bssid"] == "12:00:00:00:00:07")
        self.assertEqual(cafe["ssid"], "Cafe: Gast")  # Doppelpunkt im Namen, von nmcli maskiert
        self.assertEqual((cafe["signal_dbm"], cafe["quelle"], cafe["kanal"]), (-81, "iw", 36))
        verborgen = next(n for n in alle if n["bssid"] == "12:00:00:00:00:08")
        self.assertEqual((verborgen["ssid"], verborgen["ap"], verborgen["signal_dbm"]), ("", "", -88))

    def test_vorab_check(self):
        # Netzteil und Bluetooth kommen aus /sys und hängen vom Rechner ab – geprüft wird, was iw und ip liefern
        def run(cmd, timeout=30):
            if cmd[0] == "ip":
                return ("2: wlan0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 state UP\n"
                        "5: wg0: <POINTOPOINT,NOARP,UP,LOWER_UP> mtu 1420 state UNKNOWN\n")
            return "Power save: on\n" if cmd[-1] == "power_save" else text("info.txt")
        with mock.patch.object(c, "run", side_effect=run):
            werte, warnungen = c.vorab_check("wlan0")
        self.assertEqual((werte["kanalbreite_mhz"], werte["kanalmitte_mhz"]), (80, 5290))
        self.assertEqual((werte["power_save"], werte["vpn"]), ("on", "wg0"))
        self.assertTrue(any("Stromsparmodus ist an" in w for w in warnungen))
        self.assertTrue(any("VPN aktiv (wg0)" in w for w in warnungen))


class Iperf(unittest.TestCase):
    def test_tcp(self):
        with mock.patch.object(c, "_iperf_lauf", return_value=(json.loads(text("iperf_tcp.json")), None)) as lauf:
            self.assertEqual(c.iperf("server", reverse=True), (75296.8, None))
        self.assertEqual(lauf.call_args[0][1], ["-R"])

    def test_tcp_fehler(self):
        with mock.patch.object(c, "_iperf_lauf", return_value=(None, "iperf3: nicht erreichbar")):
            self.assertEqual(c.iperf("server", reverse=False), (None, "iperf3: nicht erreichbar"))
        with mock.patch.object(c, "_iperf_lauf", return_value=({"end": {}}, None)):
            self.assertEqual(c.iperf("server", reverse=False), (None, "iperf3 lieferte kein Ergebnis."))

    def test_udp(self):
        with mock.patch.object(c, "_iperf_lauf", return_value=(json.loads(text("iperf_udp.json")), None)) as lauf:
            self.assertEqual(c.iperf_udp("server", reverse=False), ({"jitter_ms": 0.03, "verlust_pct": 0}, None))
        self.assertEqual(lauf.call_args[0][1], ["-u", "-b", "3M", "-l", "1200"])

    def test_udp_ohne_ergebnis(self):
        with mock.patch.object(c, "_iperf_lauf", return_value=({"end": {"sum": {"packets": 0}}}, None)):
            self.assertEqual(c.iperf_udp("server", reverse=True), (None, "iperf3 lieferte kein UDP-Ergebnis."))


GUT = {"udp_up_verlust_pct": 0, "udp_down_verlust_pct": 0.1, "udp_up_jitter_ms": 1, "udp_down_jitter_ms": 1.5,
       "ping_verlust_pct": 0, "rtt_avg": 5, "rtt_max": 40, "iperf_up_mbit": 80, "iperf_down_mbit": 200,
       "signal_avg": -60}
NUR_PING = {"ping_verlust_pct": 0, "jitter_ms": 2, "rtt_avg": 5, "rtt_max": 40, "signal_avg": -60}


class Urteil(unittest.TestCase):
    def urteil(self, basis=GUT, **kw):
        return c.videocall_urteil({**basis, **kw})

    def test_gut(self):
        self.assertEqual(self.urteil(), {"stufe": "gut", "punkte": 2, "gruende": [], "basis": "Video-Test",
                                         "reserve_db": 22})

    def test_werte_aus_der_csv_sind_text(self):
        self.assertEqual(c.videocall_urteil({k: str(v) for k, v in GUT.items()})["stufe"], "gut")

    def test_ohne_werte(self):
        self.assertEqual(c.videocall_urteil({})["stufe"], "")
        self.assertIsNone(c.videocall_urteil({})["punkte"])

    def test_videoverlust(self):
        u = self.urteil(udp_up_verlust_pct=1.2)
        self.assertEqual((u["stufe"], u["gruende"]), ("geht", ["Verlust 1.2 %"]))
        self.assertEqual(self.urteil(udp_down_verlust_pct=3)["stufe"], "schlecht")

    def test_jitter(self):
        self.assertEqual(self.urteil(udp_down_jitter_ms=4)["gruende"], ["Jitter 4 ms"])
        self.assertEqual(self.urteil(udp_down_jitter_ms=12)["stufe"], "schlecht")

    def test_ping_verlust_zaehlt_auch_mit_video_test(self):
        self.assertEqual(self.urteil(ping_verlust_pct=0.7)["stufe"], "gut")  # ein einzelner verlorener Ping
        u = self.urteil(ping_verlust_pct=3)
        self.assertEqual((u["stufe"], u["gruende"]), ("geht", ["Ping-Verlust 3 %"]))
        self.assertEqual(self.urteil(ping_verlust_pct=16)["stufe"], "schlecht")

    def test_nur_ping_ist_strenger(self):
        self.assertEqual(self.urteil(NUR_PING)["stufe"], "gut")
        self.assertEqual(self.urteil(NUR_PING)["basis"], "Ping, ohne Durchsatztest")
        self.assertEqual(self.urteil(NUR_PING, ping_verlust_pct=0.7)["stufe"], "geht")
        self.assertEqual(self.urteil(NUR_PING, ping_verlust_pct=2.5)["stufe"], "schlecht")

    def test_schlechtestes_kriterium_zaehlt(self):
        u = self.urteil(udp_up_verlust_pct=1.2, iperf_up_mbit=3)
        self.assertEqual(u["stufe"], "schlecht")
        self.assertEqual(u["gruende"], ["nur 3 Mbit/s ↑", "Verlust 1.2 %"])  # schwerster Grund zuerst

    def test_roaming(self):
        self.assertEqual(self.urteil(roams=1)["gruende"], ["AP-Wechsel während der Messung"])

    def test_signalreserve(self):
        self.assertEqual(self.urteil(signal_avg=-78)["stufe"], "gut")  # knapp, aber noch gut
        self.assertEqual(self.urteil(signal_avg=-78)["reserve_db"], 4)
        u = self.urteil(signal_avg=-81)
        self.assertEqual((u["stufe"], u["gruende"], u["reserve_db"]), ("geht", ["kaum Reserve (-81 dBm)"], 1))
        self.assertEqual(self.urteil(signal_avg=-85)["reserve_db"], -3)

    def test_eine_richtung_fehlt(self):
        r = {k: v for k, v in GUT.items() if not k.startswith("udp_up")}
        self.assertEqual(c.videocall_urteil(r)["basis"], "Video-Test nur ↓ – ↑ fehlgeschlagen")

    def test_reserve_text(self):
        self.assertEqual(c.reserve_text(8), "Reserve 8 dB")
        self.assertEqual(c.reserve_text(-3), "3 dB unter dem Grenzbereich")
        self.assertEqual(c.reserve_text(None), "")

    def test_anzeige(self):
        self.assertEqual(anzeige.urteil_zeile(self.urteil()), ("● gut · Reserve 22 dB  (Basis: Video-Test)", "gut"))
        self.assertIn("Reserve 4 dB (knapp)", anzeige.urteil_zeile(self.urteil(signal_avg=-78))[0])
        kaum = anzeige.urteil_zeile(self.urteil(signal_avg=-81))
        self.assertEqual(kaum, ("◐ geht – kaum Reserve (-81 dBm)  (Basis: Video-Test)", "mittel"))
        self.assertEqual(anzeige.urteil_zeile(c.videocall_urteil({})), ("?", ""))


class Zaehler(unittest.TestCase):
    VOR = {"bssid": AP1, "tx_packets": 1000, "tx_retries": 100, "tx_failed": 1}

    def test_differenz(self):
        nach = {"bssid": AP1, "tx_packets": 5000, "tx_retries": 500, "tx_failed": 3}
        self.assertEqual(c.last_zaehler(self.VOR, nach), {"last_tx_packets": 4000, "last_tx_retries": 400,
                                                         "last_tx_failed": 2, "last_retry_pct": 10.0})

    def test_unbrauchbar(self):
        nach = {"bssid": AP1, "tx_packets": 5000, "tx_retries": 500, "tx_failed": 3}
        self.assertEqual(c.last_zaehler(self.VOR, {**nach, "bssid": AP2}), {})      # Roaming
        self.assertEqual(c.last_zaehler(self.VOR, {**nach, "tx_retries": 50}), {})  # Zähler zurückgesetzt
        self.assertEqual(c.last_zaehler(self.VOR, {**nach, "tx_packets": 1000}), {})  # nichts gesendet
        self.assertEqual(c.last_zaehler(None, nach), {})


class Kleben(unittest.TestCase):
    def aps(self, anderer_dbm, anderer_freq=5500):
        return [{"ap": "Flur", "bssid": AP1, "freq_mhz": 5260, "signal_dbm": -75, "verbunden": "ja"},
                {"ap": "Büro", "bssid": AP2, "freq_mhz": anderer_freq, "signal_dbm": anderer_dbm, "verbunden": ""}]

    def test_staerkerer_ap_im_selben_band(self):
        self.assertEqual(c.klebt(self.aps(-60)), ("Büro", 15))
        self.assertEqual(c.klebe_warnung(self.aps(-60)), "Büro wäre 15 dB stärker – Client klebt am aktuellen AP")

    def test_kein_hinweis(self):
        self.assertIsNone(c.klebt(self.aps(-68)))        # nur 7 dB stärker
        self.assertIsNone(c.klebt(self.aps(-50, 2437)))  # 2,4 GHz reicht ohnehin weiter
        self.assertIsNone(c.klebt([]))


class Dateien(unittest.TestCase):
    def setUp(self):
        leeren()

    def test_neue_spalten_ergaenzen(self):
        p = c.DATA / "x.csv"
        c.append_csv(p, ["id", "a"], [{"id": "1", "a": "eins"}])
        c.append_csv(p, ["id", "a", "b"], [{"id": "2", "a": None, "b": 0}])
        self.assertEqual(c.read_csv(p), [{"id": "1", "a": "eins", "b": ""}, {"id": "2", "a": "", "b": "0"}])

    def test_ap_namen(self):
        c.APS.write_text("# Kommentar\n02:00:00:00:00 = Fritzbox\n02:00:00:00:00:02 = Fritzbox 2,4\n")
        self.assertEqual(c.ap_name(AP1), "Fritzbox")
        self.assertEqual(c.ap_name(AP2.upper()), "Fritzbox 2,4")  # der längste passende Präfix gewinnt
        self.assertEqual(c.ap_name(AP3), AP3)
        self.assertEqual(c.ap_name(None), "")

    def test_szenarien(self):
        c.szenario_speichern("Fritzbox", "Router im Flur")
        c.szenario_aktivieren("cAP Decke")
        self.assertEqual(c.lade_szenarien(), {"Fritzbox": "Router im Flur", "cAP Decke": ""})
        self.assertEqual(c.aktives_szenario(), "cAP Decke")


class Kanaele(unittest.TestCase):
    def test_kanal_name(self):
        self.assertEqual(c.kanal_name(2412), "2.4 GHz Ch1")
        self.assertEqual(c.kanal_name(2484), "2.4 GHz Ch14")
        self.assertEqual(c.kanal_name(5260.0), "5 GHz Ch52")
        self.assertEqual(c.kanal_name(5955), "6 GHz Ch1")
        self.assertEqual(c.kanal_name(None), "?")

    def test_band(self):
        self.assertEqual([c.band(f) for f in (2462, 5180, 5885, 5955, None)], ["2.4", "5", "5", "6", ""])

    def test_num(self):
        self.assertEqual(c.num("5260 MHz"), 5260)
        self.assertEqual(c.num("-62.5"), -62.5)
        self.assertEqual(c.num(0), 0)
        self.assertIsNone(c.num(""))
        self.assertIsNone(c.num(None))


if __name__ == "__main__":
    unittest.main()
