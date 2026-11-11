"""Messlogik und Datenhaltung für wifi-survey (ohne Ein-/Ausgabe)."""

import csv
import json
import os
import re
import signal
import statistics
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from . import ansage


def _datenordner():
    if os.environ.get("WIFI_SURVEY_DIR"):
        return Path(os.environ["WIFI_SURVEY_DIR"])
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "wifi-survey"


DATA = _datenordner()
DATA.mkdir(parents=True, exist_ok=True)
ORTE = DATA / "orte.txt"
APS = DATA / "aps.txt"
SZENARIEN = DATA / "szenarien.txt"
AKTIV = DATA / ".aktives-szenario"
CONFIG = DATA / "config.json"
MESSUNGEN = DATA / "messungen.csv"
SAMPLES = DATA / "samples.csv"
SCANS = DATA / "scans.csv"
NETZE = DATA / "netze.csv"

ENV = {**os.environ, "LC_ALL": "C"}

MESS_FELDER = [
    "id", "zeit", "szenario", "ort", "notiz", "dauer_s", "ssid", "bssid", "ap", "freq_mhz", "band",
    "signal_min", "signal_avg", "signal_max", "beacon_signal_avg",
    "tx_bitrate_avg", "rx_bitrate_avg",
    "tx_packets", "tx_retries", "retry_quote_pct", "tx_failed", "beacon_loss", "rx_drop_misc",
    "roams", "ping_ziel", "ping_gesendet", "ping_verlust_pct",
    "rtt_min", "rtt_avg", "rtt_max", "jitter_ms",
    "iperf_down_mbit", "iperf_up_mbit",
    "bester_ap", "bester_ap_signal", "bester_ap_freq",
    # WLAN-Zähler während des iperf-Uploads (viele Pakete -> aussagekräftige Retry-Quote)
    "last_tx_packets", "last_tx_retries", "last_retry_pct", "last_tx_failed",
    # UDP-Strom wie bei einer Videokonferenz (UDP_MBIT, UDP_LAENGE), ↑ = Laptop sendet
    "udp_up_jitter_ms", "udp_up_verlust_pct", "udp_down_jitter_ms", "udp_down_verlust_pct",
    # Vorab-Check: Zustand des Laptops und genaue Lage der Verbindung
    "netzteil", "power_save", "vpn", "bluetooth", "kanalbreite_mhz", "kanalmitte_mhz",
]

UDP_MBIT = 3       # typischer Videostrom einer Konferenz (720p–1080p) je Richtung
UDP_LAENGE = 1200  # Bytes je Paket, wie RTP-Video
UDP_DAUER = 10     # Sekunden je Richtung
IPERF_DAUER = 10   # Sekunden je Richtung beim TCP-Durchsatztest
SCAN_DAUER = 6     # grobe Schätzung für den AP-Scan (nur für die Restzeit-Ansage)
SAMPLE_FELDER = ["id", "ort", "sek", "bssid", "signal", "tx_bitrate", "rx_bitrate", "tx_retries", "tx_failed"]
SCAN_FELDER = ["id", "ort", "bssid", "ap", "ssid", "freq_mhz", "signal_dbm", "quelle", "verbunden"]
# alle sichtbaren Netze inkl. Nachbarn – dokumentiert die Kanalbelegung zum Messzeitpunkt
NETZ_FELDER = ["id", "ort", "ssid", "bssid", "ap", "kanal", "freq_mhz", "band", "bandbreite_mhz",
               "signal_dbm", "quelle", "verbunden"]

METRIKEN = {  # name: (spalte, beschriftung, höher_ist_besser, einheit, toleranz)
    # toleranz: kleinere Unterschiede gelten als Messrauschen (kein Sieger)
    "signal": ("signal_avg", "Signal Ø", True, "dBm", 3),
    "min": ("signal_min", "Signal min", True, "dBm", 3),
    "loss": ("ping_verlust_pct", "Paketverlust", False, "%", 0.5),
    "rtt": ("rtt_avg", "Ping RTT Ø", False, "ms", 2),
    "jitter": ("jitter_ms", "Jitter", False, "ms", 1),
    "video": ("video_score", "Videocall-Eignung (2 gut · 1 geht · 0 schlecht)", True, "Stufe", 0.5),
    "retry": ("retry_quote_pct", "Retry-Quote Ping-Phase (unsicher)", False, "%", 5),
    "retry_last": ("last_retry_pct", "Retry-Quote unter Last", False, "%", 5),
    "udp_jitter": ("udp_up_jitter_ms", "UDP-Jitter ↑", False, "ms", 1),
    "udp_loss": ("udp_up_verlust_pct", "UDP-Verlust ↑", False, "%", 0.5),
    "udp_jitter_down": ("udp_down_jitter_ms", "UDP-Jitter ↓", False, "ms", 1),
    "udp_loss_down": ("udp_down_verlust_pct", "UDP-Verlust ↓", False, "%", 0.5),
    "bitrate": ("rx_bitrate_avg", "RX-Bitrate", True, "Mbit/s", 50),
    "down": ("iperf_down_mbit", "iperf Download", True, "Mbit/s", 20),
    "up": ("iperf_up_mbit", "iperf Upload", True, "Mbit/s", 20),
}

STANDARD_ORTE = ["Wohnzimmer", "Küche", "Schlafzimmer", "Arbeitszimmer", "Bad", "Flur", "Balkon"]


# --------------------------------------------------------------------------- Hilfen

def run(cmd, timeout=30):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, env=ENV, timeout=timeout).stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def num(s):
    m = re.search(r"-?\d+(?:\.\d+)?", str(s) if s is not None else "")
    return float(m.group()) if m else None


def band(freq):
    if not freq:
        return ""
    return "2.4" if freq < 3000 else ("5" if freq < 5925 else "6")


def kanal_name(freq):
    if not freq:
        return "?"
    f = int(freq)
    if 2412 <= f <= 2472:
        ch = (f - 2407) // 5
    elif f == 2484:
        ch = 14
    elif 5000 <= f < 5925:
        ch = (f - 5000) // 5
    else:
        ch = (f - 5950) // 5
    return f"{band(freq)} GHz Ch{ch}"


def avg(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.mean(xs), 1) if xs else None


def bewertung_signal(dbm):
    """-> (text, stufe) mit stufe in gut|mittel|schlecht|''"""
    if dbm is None:
        return "?", ""
    if dbm >= -60:
        return "sehr gut", "gut"
    if dbm >= -67:
        return "gut", "gut"
    if dbm >= -75:
        return "mäßig", "mittel"
    return "schlecht", "schlecht"


def stufe_loss(v):
    if v is None:
        return ""
    return "gut" if v == 0 else ("mittel" if v < 2 else "schlecht")


def stufe_retry(v):
    if v is None:
        return ""
    return "gut" if v < 10 else ("mittel" if v < 25 else "schlecht")


def stufe_udp_jitter(v):
    """iperf3-Jitter (RFC 3550) im eigenen Netz – für Video sollte er klar unter 10 ms bleiben."""
    if v is None:
        return ""
    return "gut" if v < 3 else ("mittel" if v < 10 else "schlecht")


def stufe_udp_loss(v):
    """Videokonferenzen verkraften Einzelverluste, ab ~1–2 % wird das Bild sichtbar schlechter."""
    if v is None:
        return ""
    return "gut" if v < 0.5 else ("mittel" if v < 2 else "schlecht")


# --------------------------------------------------------------------------- Videocall-Urteil

STUFEN = ("schlecht", "geht", "gut")  # Index = Punkte


def videocall_urteil(r):
    """Eignung einer Messung für Videokonferenzen.

    -> {"stufe": gut|geht|schlecht|"", "punkte": 2|1|0|None, "gruende": [...], "basis": text}
    Maßgeblich ist das schlechteste Einzelkriterium. Fehlen Werte, wird nur bewertet, was da ist.
    """
    befunde = []  # (punkte, grund)

    def pruefe(wert, gut, geht, grund, hoch_gut=False):
        """grund: Vorlage mit {} für den Wert – wird nur bei vorhandenem Wert ausgefüllt."""
        if wert is None:
            return
        ok = (lambda g: wert >= g) if hoch_gut else (lambda g: wert < g)
        text = grund.format(f"{wert:g}")
        befunde.append((2, None) if ok(gut) else (1, text) if ok(geht) else (0, text))

    udp_v = [v for v in (num(r.get("udp_up_verlust_pct")), num(r.get("udp_down_verlust_pct"))) if v is not None]
    udp_j = [v for v in (num(r.get("udp_up_jitter_ms")), num(r.get("udp_down_jitter_ms"))) if v is not None]
    if udp_v or udp_j:
        basis = "Video-Test"
        fehlt = [pf for pf, k in (("↑", "udp_up_jitter_ms"), ("↓", "udp_down_jitter_ms")) if num(r.get(k)) is None]
        if fehlt:
            basis += f" nur {'↓' if fehlt == ['↑'] else '↑'} – {fehlt[0]} fehlgeschlagen"
        pruefe(max(udp_v, default=None), 0.5, 2, "Verlust {} %")
        pruefe(max(udp_j, default=None), 3, 10, "Jitter {} ms")
    else:
        basis = "Ping"
        pruefe(num(r.get("ping_verlust_pct")), 0.01, 2, "Ping-Verlust {} %")
        pruefe(num(r.get("jitter_ms")), 10, 30, "Ping-Jitter {} ms")
    pruefe(num(r.get("rtt_avg")), 20, 50, "RTT {} ms")
    pruefe(num(r.get("rtt_max")), 150, 400, "Ping-Spitze {} ms")
    pruefe(num(r.get("iperf_up_mbit")), 10, 4, "nur {} Mbit/s ↑", hoch_gut=True)
    pruefe(num(r.get("iperf_down_mbit")), 10, 4, "nur {} Mbit/s ↓", hoch_gut=True)
    if int(num(r.get("roams")) or 0):
        befunde.append((1, "AP-Wechsel während der Messung"))
    sig = num(r.get("signal_avg"))
    if sig is not None and sig < -80:
        befunde.append((1, f"kaum Reserve ({sig:g} dBm)"))
    if not befunde:
        return {"stufe": "", "punkte": None, "gruende": [], "basis": basis}
    punkte = min(p for p, _ in befunde)
    if num(r.get("iperf_up_mbit")) is None:
        basis += ", ohne Durchsatztest"
    return {"stufe": STUFEN[punkte], "punkte": punkte, "basis": basis,
            "gruende": [g for p, g in sorted(befunde, key=lambda b: b[0]) if g and p < 2]}


def stufe_ampel(stufe):
    """Urteilsstufe -> Farbstufe der Oberflächen (gut|mittel|schlecht)."""
    return {"gut": "gut", "geht": "mittel", "schlecht": "schlecht"}.get(stufe, "")


# --------------------------------------------------------------------------- Vorab-Check

VPN_PRAEFIXE = ("wg", "tun", "tap", "tailscale", "zt", "nordlynx", "proton", "ppp", "mullvad")


def vorab_check(iface):
    """Zustand des Laptops, der Messwerte verfälschen kann. Schnell (nur sysfs/iw/ip), ohne Root.

    -> (werte für messungen.csv, Liste von Warnungen)
    """
    w = {}
    netz, akku = None, False
    for ps in Path("/sys/class/power_supply").glob("*"):
        try:
            typ = (ps / "type").read_text().strip()
            if typ == "Mains":
                netz = (ps / "online").read_text().strip() == "1"
            elif typ == "Battery":
                akku = True
        except OSError:
            pass
    w["netzteil"] = "" if not akku or netz is None else ("ja" if netz else "nein")
    m = re.search(r"Power save:\s*(\w+)", run(["iw", "dev", iface, "get", "power_save"], timeout=5))
    w["power_save"] = m.group(1) if m else ""
    vpn = [l.split(": ")[1].split("@")[0] for l in run(["ip", "-o", "link", "show", "up"], timeout=5).splitlines()
           if ": " in l and l.split(": ")[1].startswith(VPN_PRAEFIXE)]
    w["vpn"] = " ".join(vpn)
    bt = ""
    for rf in Path("/sys/class/rfkill").glob("rfkill*"):
        try:
            if (rf / "type").read_text().strip() == "bluetooth":
                frei = (rf / "soft").read_text().strip() == "0" and (rf / "hard").read_text().strip() == "0"
                bt = "an" if frei or bt == "an" else "aus"
        except OSError:
            pass
    w["bluetooth"] = bt
    info = run(["iw", "dev", iface, "info"], timeout=5)
    m = re.search(r"width:\s*(\d+)\s*MHz", info)
    w["kanalbreite_mhz"] = int(m.group(1)) if m else None
    m = re.search(r"center1:\s*(\d+)\s*MHz", info)
    w["kanalmitte_mhz"] = int(m.group(1)) if m else None
    m = re.search(r"channel \d+ \((\d+) MHz\)", info)
    frequenz = int(m.group(1)) if m else None

    warn = []
    if w["power_save"] == "on":
        warn.append(f"WLAN-Stromsparmodus ist an – verfälscht RTT und Jitter "
                    f"(abschalten: sudo iw dev {iface} set power_save off)")
    if w["netzteil"] == "nein":
        warn.append("Laptop läuft auf Akku – Energiesparfunktionen können Ping und Jitter verschlechtern")
    if w["vpn"]:
        warn.append(f"VPN aktiv ({w['vpn']}) – Ping/iperf laufen evtl. durch den Tunnel")
    if w["bluetooth"] == "an" and band(frequenz) == "2.4":
        warn.append("Bluetooth ist an und teilt sich 2,4 GHz (und die Antenne) mit dem WLAN")
    return w, warn


# --------------------------------------------------------------------------- CSV

def append_csv(path, felder, rows):
    if path.exists():
        with path.open(newline="") as f:
            alt = csv.DictReader(f)
            if alt.fieldnames != felder:  # neue Spalten -> Datei umschreiben
                bestand = list(alt)
                schreibe_csv(path, felder, bestand)
    neu = not path.exists()
    with path.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=felder)
        if neu:
            w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in felder})


def schreibe_csv(path, felder, rows):
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", newline="") as g:
        w = csv.DictWriter(g, fieldnames=felder)
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in felder})
    tmp.replace(path)


def read_csv(path):
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def messung_loeschen(mid):
    for path, felder in ((MESSUNGEN, MESS_FELDER), (SAMPLES, SAMPLE_FELDER), (SCANS, SCAN_FELDER),
                         (NETZE, NETZ_FELDER)):
        rows = read_csv(path)
        rest = [r for r in rows if r.get("id") != mid]
        if len(rest) != len(rows):
            schreibe_csv(path, felder, rest)


# --------------------------------------------------------------------------- Konfiguration

def lade_config():
    cfg = {"dauer": 30, "iperf_host": "", "ping_ziel": "", "scan": True, "udp": True, "countdown": 0,
           "dauertest_min": 15}
    try:
        cfg.update(json.loads(CONFIG.read_text()))
    except (FileNotFoundError, ValueError):
        pass
    return cfg


def speichere_config(cfg):
    CONFIG.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")


# --------------------------------------------------------------------------- Orte

def lade_orte():
    if not ORTE.exists():
        speichere_orte(STANDARD_ORTE)
    return [l.strip() for l in ORTE.read_text().splitlines() if l.strip() and not l.startswith("#")]


def speichere_orte(orte):
    ORTE.write_text("# Ein Ort pro Zeile – Reihenfolge = Reihenfolge in Report und Vergleich\n"
                    + "".join(o + "\n" for o in orte))


def ort_hinzufuegen(ort):
    orte = lade_orte()
    if ort and ort not in orte:
        speichere_orte(orte + [ort])


# --------------------------------------------------------------------------- APs

def lade_aps():
    namen = {}
    if APS.exists():
        for l in APS.read_text().splitlines():
            if "=" in l and not l.lstrip().startswith("#"):
                k, v = l.split("=", 1)
                namen[k.strip().lower()] = v.strip()
    return namen


def ap_name(bssid, namen=None):
    if not bssid:
        return ""
    namen = lade_aps() if namen is None else namen
    b = bssid.lower()
    best = ""
    for prefix in namen:
        if b.startswith(prefix) and len(prefix) > len(best):
            best = prefix
    return namen[best] if best else bssid


def ap_benennen(bssid, name):
    """Benennt alle Funkzellen eines Geräts (Präfix ohne letztes Oktett)."""
    prefix = ":".join(bssid.lower().split(":")[:5])
    zeilen = APS.read_text().splitlines() if APS.exists() else ["# BSSID-Präfix = Name"]
    zeilen = [z for z in zeilen if z.partition("=")[0].strip().lower() != prefix]
    zeilen.append(f"{prefix} = {name}")
    APS.write_text("\n".join(zeilen) + "\n")


# --------------------------------------------------------------------------- Szenarien

def lade_szenarien():
    sz = {}
    if SZENARIEN.exists():
        for l in SZENARIEN.read_text().splitlines():
            l = l.strip()
            if l and not l.startswith("#"):
                name, _, beschr = l.partition("=")
                sz[name.strip()] = beschr.strip()
    return sz


def szenario_speichern(name, beschreibung=""):
    name = name.replace("=", "-").strip()
    sz = lade_szenarien()
    sz[name] = beschreibung if beschreibung or name not in sz else sz[name]
    SZENARIEN.write_text("# Name = Beschreibung des Setups (Geräte, Standorte, Kanäle, Anbindung …)\n"
                         + "".join(f"{n} = {b}\n" if b else f"{n}\n" for n, b in sz.items()))
    return name


def aktives_szenario():
    try:
        return AKTIV.read_text().strip() or None
    except FileNotFoundError:
        return None


def szenario_aktivieren(name):
    if name not in lade_szenarien():
        szenario_speichern(name)
    AKTIV.write_text(name + "\n")


def anzahl_messungen():
    """-> {(szenario, ort): n}"""
    anz = {}
    for r in read_csv(MESSUNGEN):
        k = (r.get("szenario", ""), r["ort"])
        anz[k] = anz.get(k, 0) + 1
    return anz


# --------------------------------------------------------------------------- WLAN auslesen

def wlan_interface():
    m = re.findall(r"Interface (\S+)", run(["iw", "dev"]))
    return m[0] if m else None


def station(iface):
    out = run(["iw", "dev", iface, "station", "dump"], timeout=5)
    if not out.strip():
        return None
    d = {"bssid": (re.search(r"Station (\S+)", out) or [None, None])[1]}
    for key, feld in [("signal", r"^\s*signal:\s*(-?\d+)"), ("signal_avg", r"signal avg:\s*(-?\d+)"),
                      ("beacon_signal_avg", r"beacon signal avg:\s*(-?\d+)"),
                      ("tx_bitrate", r"tx bitrate:\s*([\d.]+)"), ("rx_bitrate", r"rx bitrate:\s*([\d.]+)"),
                      ("tx_packets", r"tx packets:\s*(\d+)"), ("tx_retries", r"tx retries:\s*(\d+)"),
                      ("tx_failed", r"tx failed:\s*(\d+)"), ("beacon_loss", r"beacon loss:\s*(\d+)"),
                      ("rx_drop_misc", r"rx drop misc:\s*(\d+)")]:
        m = re.search(feld, out, re.M)
        d[key] = float(m.group(1)) if m else None
    return d


def link(iface):
    out = run(["iw", "dev", iface, "link"], timeout=5)
    ssid = re.search(r"SSID: (.*)", out)
    freq = re.search(r"freq: ([\d.]+)", out)
    return (ssid.group(1).strip() if ssid else None, float(freq.group(1)) if freq else None)


def gateway(iface):
    m = re.search(r"default via (\S+)", run(["ip", "route", "show", "default", "dev", iface]))
    return m.group(1) if m else None


def scan(iface, ssid, namen=None, verbunden=None, alle=None):
    """Alle APs des eigenen Netzes mit echten dBm-Werten (ohne Root).

    alle: optionale Liste, in die zusätzlich *alle* sichtbaren Netze (auch fremde) geschrieben werden.
    """
    namen = lade_aps() if namen is None else namen
    # nmcli löst einen vollständigen Scan aus und wartet, bis er fertig ist
    nm = run(["nmcli", "-t", "-f", "BSSID,SSID,CHAN,FREQ,BANDWIDTH,SIGNAL", "dev", "wifi", "list",
              "--rescan", "yes", "ifname", iface], timeout=40)
    netze = {}
    for l in nm.splitlines():
        teile = re.split(r"(?<!\\):", l)
        if len(teile) < 6:
            continue
        bssid = teile[0].replace("\\", "").lower()
        q = num(teile[5])
        # NetworkManager-Prozent zurück in dBm (Umkehrung von nm_wifi_utils_level_to_quality)
        dbm = round(-40 - (100 - q) * 0.6) if q is not None else None
        ganz = lambda x: int(x) if x is not None else None  # noqa: E731
        netze[bssid] = {"bssid": bssid, "ssid": teile[1].replace("\\:", ":"), "kanal": ganz(num(teile[2])),
                        "freq_mhz": num(teile[3]), "bandbreite_mhz": ganz(num(teile[4])), "signal_dbm": dbm,
                        "quelle": "nmcli≈"}
    cur = None
    for l in run(["iw", "dev", iface, "scan", "dump"]).splitlines():
        if l.startswith("BSS "):
            cur = {"bssid": l.split()[1].split("(")[0].lower()}
        elif cur is not None:
            if "freq:" in l:
                cur["freq_mhz"] = num(l)
            elif l.strip().startswith("signal:"):
                cur["signal_dbm"] = num(l)
            elif l.strip().startswith("SSID:"):
                b = cur["bssid"]
                netze[b] = {**netze.get(b, {}), **cur, "ssid": l.split("SSID:", 1)[1].strip(), "quelle": "iw"}
    for a in netze.values():
        a["ap"] = ap_name(a["bssid"], namen) if a["bssid"] != ap_name(a["bssid"], namen) else ""
        a["verbunden"] = "ja" if a["bssid"] == (verbunden or "").lower() else ""
        a["band"] = band(a.get("freq_mhz"))
    sortiert = sorted(netze.values(), key=lambda a: -(a.get("signal_dbm") or -999))
    if alle is not None:
        alle.extend(sortiert)
    aps = [dict(a) for a in sortiert if a["ssid"] == ssid]
    for a in aps:
        a["ap"] = a["ap"] or a["bssid"]
    return aps


IPERF_PORT = 5201


def iperf_ziel(text):
    """'host' oder 'host:port' -> (host, port)"""
    text = (text or "").strip()
    if text.count(":") == 1:
        h, _, p = text.partition(":")
        if p.isdigit():
            return h, int(p)
    return text, IPERF_PORT


def iperf_pruefen(text, timeout=2):
    """Schnelle Vorprüfung ohne Messung. -> Fehlertext oder None"""
    import shutil
    import socket
    if not shutil.which("iperf3"):
        return "iperf3 ist auf diesem Rechner nicht installiert (sudo pacman -S iperf3)."
    host, port = iperf_ziel(text)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return None
    except socket.gaierror:
        return f"Hostname „{host}“ unbekannt."
    except OSError:
        return f"Kein iperf3-Server auf {host}:{port} erreichbar – läuft dort „iperf3 -s“?"


IPERF_VERSUCHE = 3  # über WLAN geht gelegentlich das Startpaket eines UDP-Tests verloren


def _iperf_lauf(text, extra, stop=None, dauer=10):
    """Startet iperf3 mit -J, bei Fehlern bis zu IPERF_VERSUCHE-mal.
    -> (JSON-Dict oder None, Fehlertext oder None)"""
    for versuch in range(IPERF_VERSUCHE):
        j, fehler = _iperf_einmal(text, extra, stop, dauer)
        if not fehler or fehler in ("abgebrochen", "iperf3 ist nicht installiert.") or (stop and stop.is_set()):
            break
        time.sleep(1 + versuch)  # Server Zeit geben, den vorigen Test abzuschließen
    return j, fehler


def _iperf_einmal(text, extra, stop, dauer):
    host, port = iperf_ziel(text)
    cmd = ["iperf3", "-c", host, "-p", str(port), "-t", str(dauer), "-J"] + extra
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=ENV)
    except FileNotFoundError:
        return None, "iperf3 ist nicht installiert."
    while p.poll() is None:
        if stop is not None and stop.is_set():
            p.terminate()
            return None, "abgebrochen"
        time.sleep(0.2)
    try:
        j = json.loads(p.stdout.read() or "{}")
    except ValueError:
        return None, "iperf3 lieferte keine auswertbare Antwort."
    if j.get("error"):
        fehler = j["error"]
        if "busy" in fehler:
            fehler = "Server ist gerade mit einem anderen Test beschäftigt."
        return None, f"iperf3: {fehler}"
    return j, None


def iperf(text, reverse, stop=None, dauer=IPERF_DAUER):
    """TCP-Durchsatz. -> (Mbit/s oder None, Fehlertext oder None)"""
    j, fehler = _iperf_lauf(text, ["-R"] if reverse else [], stop, dauer)
    if fehler:
        return None, fehler
    try:
        return round(j["end"]["sum_received"]["bits_per_second"] / 1e6, 1), None
    except KeyError:
        return None, "iperf3 lieferte kein Ergebnis."


def iperf_udp(text, reverse, stop=None, dauer=UDP_DAUER, mbit=UDP_MBIT):
    """Gleichmäßiger UDP-Strom wie ein Videocall. reverse=True: Server sendet, Laptop empfängt.
    -> ({"jitter_ms", "verlust_pct"} oder None, Fehlertext oder None)"""
    extra = ["-u", "-b", f"{mbit}M", "-l", str(UDP_LAENGE)] + (["-R"] if reverse else [])
    j, fehler = _iperf_lauf(text, extra, stop, dauer)
    if fehler:
        return None, fehler
    end = j.get("end", {})
    s = end.get("sum_received") or end.get("sum")  # Werte des Empfängers
    if not s or s.get("jitter_ms") is None:
        return None, "iperf3 lieferte kein UDP-Ergebnis."
    return {"jitter_ms": round(s["jitter_ms"], 2), "verlust_pct": round(s.get("lost_percent") or 0, 2)}, None


def iperf_schnelltest(text):
    """3-Sekunden-Test in beide Richtungen. -> (down, up, fehler)"""
    fehler = iperf_pruefen(text)
    if fehler:
        return None, None, fehler
    down, f1 = iperf(text, reverse=True, dauer=3)
    up, f2 = iperf(text, reverse=False, dauer=3)
    return down, up, f1 or f2


# --------------------------------------------------------------------------- Messung

class Abgebrochen(Exception):
    pass


def messung(*args, countdown=0, **kw):
    """Führt eine Messung durch. Gibt (ergebnis, samples, aps) zurück, speichert nicht.

    on_sample(sek, station_dict) wird jede Sekunde aufgerufen, on_status(text) bei Phasenwechseln.
    stop: threading.Event zum Abbrechen (wirft Abgebrochen).
    countdown: Sekunden Vorlauf mit Signaltönen und Sprachansagen (0 = sofort, ohne Ansagen).
    """
    return ansage.mit_ansage(countdown, lambda ans: _messung(*args, ans=ans, **kw), Abgebrochen)


def _messung(szenario, ort, notiz="", dauer=30, iface=None, ping_ziel=None, iperf_host=None,
             mit_scan=True, mit_udp=True, on_sample=None, on_status=None, stop=None, ans=None):
    stop = stop or threading.Event()
    say = on_status or (lambda t: None)
    iface = iface or wlan_interface()
    if not iface:
        raise RuntimeError("Kein WLAN-Interface gefunden.")
    namen = lade_aps()
    ssid, freq = link(iface)
    if not ssid:
        raise RuntimeError("Nicht mit einem WLAN verbunden.")
    if iperf_host:
        say("Prüfe iperf3-Server …")
        fehler = iperf_pruefen(iperf_host)
        if fehler:  # lieber sofort abbrechen als 30 s messen und dann ohne Durchsatz dastehen
            raise RuntimeError(fehler + " Feld leeren, um ohne iperf3 zu messen.")
    ziel = ping_ziel or gateway(iface)
    vorab, vorab_warn = vorab_check(iface)
    for t in vorab_warn:
        say("⚠ " + t)

    # geschätzte Dauer der Phasen nach der Signalmessung – für die Restzeit-Ansagen
    t_iperf = IPERF_DAUER + 1 if iperf_host else 0
    t_udp = UDP_DAUER + 1 if iperf_host and mit_udp else 0
    t_scan = SCAN_DAUER if mit_scan else 0

    def plan(rest):
        if ans:
            ans.rest(rest)

    if ans:
        ans.runterzaehlen(stop, say, Abgebrochen)
        ans.starten(dauer + 2 * t_iperf + 2 * t_udp + t_scan)
    mid = datetime.now().strftime("%Y%m%d-%H%M%S")

    ping_erg = {}
    ping = None
    if ziel:
        ping = subprocess.Popen(["ping", "-n", "-i", "0.2", "-w", str(dauer), ziel],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=ENV)

    say(f"Messe {dauer} s …")
    samples, start_st = [], station(iface)
    bssids, freqs = [], []
    t0 = time.time()
    try:
        while time.time() - t0 < dauer:
            if stop.is_set():
                raise Abgebrochen()
            st = station(iface)
            _, f = link(iface)
            sek = int(time.time() - t0)
            if st:
                samples.append({"id": mid, "ort": ort, "sek": sek, **{k: st.get(k) for k in SAMPLE_FELDER if k in st}})
                if not bssids or bssids[-1] != st["bssid"]:
                    bssids.append(st["bssid"])
                freqs.append(f)
            if on_sample:
                on_sample(sek, st, ap_name(st["bssid"], namen) if st else None)
            # bis zur nächsten vollen Sekunde warten, aber Abbruch schnell erkennen
            nxt = t0 + sek + 1
            while time.time() < nxt and not stop.is_set():
                time.sleep(0.05)
    except Abgebrochen:
        if ping:
            ping.kill()
        raise

    if ping:
        say("Werte Ping aus …")
        try:
            out, _ = ping.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            ping.send_signal(signal.SIGINT)
            out, _ = ping.communicate()
        m = re.search(r"(\d+) packets transmitted, (\d+) received", out)
        if m:
            tx, rx = int(m.group(1)), int(m.group(2))
            ping_erg["ping_gesendet"] = tx
            ping_erg["ping_verlust_pct"] = round(100 * (tx - rx) / tx, 1) if tx else None
        m = re.search(r"= ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+) ms", out)
        if m:
            ping_erg.update(rtt_min=round(float(m.group(1)), 1), rtt_avg=round(float(m.group(2)), 1),
                            rtt_max=round(float(m.group(3)), 1), jitter_ms=round(float(m.group(4)), 1))
    end_st = station(iface) or {}

    def delta(k):
        a, b = (start_st or {}).get(k), end_st.get(k)
        # bei Roaming werden die Zähler zurückgesetzt -> nur gültig ohne AP-Wechsel
        return int(b - a) if a is not None and b is not None and b >= a and len(bssids) == 1 else None

    sig = [s["signal"] for s in samples if s.get("signal") is not None]
    r = {"id": mid, "zeit": datetime.now().isoformat(timespec="seconds"), "szenario": szenario,
         "ort": ort, "notiz": notiz, "dauer_s": dauer, "ssid": ssid,
         "bssid": bssids[-1] if bssids else None, "freq_mhz": freqs[-1] if freqs else freq,
         "signal_min": min(sig) if sig else None, "signal_avg": avg(sig), "signal_max": max(sig) if sig else None,
         "beacon_signal_avg": end_st.get("beacon_signal_avg"),
         "tx_bitrate_avg": avg([s.get("tx_bitrate") for s in samples]),
         "rx_bitrate_avg": avg([s.get("rx_bitrate") for s in samples]),
         "tx_packets": delta("tx_packets"), "tx_retries": delta("tx_retries"), "tx_failed": delta("tx_failed"),
         "beacon_loss": delta("beacon_loss"), "rx_drop_misc": delta("rx_drop_misc"),
         "roams": max(len(bssids) - 1, 0), "ping_ziel": ziel, **ping_erg, **vorab,
         "vorab_warnungen": vorab_warn}
    r["ap"] = ap_name(r["bssid"], namen)
    r["band"] = band(r["freq_mhz"])
    if r["tx_packets"]:
        r["retry_quote_pct"] = round(100 * (r["tx_retries"] or 0) / r["tx_packets"], 1)

    if iperf_host:
        say(f"iperf3 → {iperf_host}: Download …")
        plan(2 * t_iperf + 2 * t_udp + t_scan)
        r["iperf_down_mbit"], f1 = iperf(iperf_host, reverse=True, stop=stop)
        if stop.is_set():
            raise Abgebrochen()
        say(f"iperf3 → {iperf_host}: Upload …")
        plan(t_iperf + 2 * t_udp + t_scan)
        vor = station(iface)
        r["iperf_up_mbit"], f2 = iperf(iperf_host, reverse=False, stop=stop)
        if stop.is_set():
            raise Abgebrochen()
        r.update(last_zaehler(vor, station(iface)) if not f2 else {})
        fehler = [f for f in (f1, f2) if f]
        if mit_udp:
            for richtung, reverse in (("up", False), ("down", True)):
                say(f"iperf3 UDP {UDP_MBIT} Mbit/s {'↑' if richtung == 'up' else '↓'} (wie Videocall) …")
                plan((2 if richtung == "up" else 1) * t_udp + t_scan)
                u, fu = iperf_udp(iperf_host, reverse=reverse, stop=stop)
                if stop.is_set():
                    raise Abgebrochen()
                if u:
                    r[f"udp_{richtung}_jitter_ms"] = u["jitter_ms"]
                    r[f"udp_{richtung}_verlust_pct"] = u["verlust_pct"]
                elif fu:
                    fehler.append(fu)
        if fehler:
            r["iperf_fehler"] = fehler[0]  # wird angezeigt, aber nicht in der CSV gespeichert

    aps = []
    if mit_scan:
        say("Scanne APs …")
        plan(t_scan)
        netze = []
        aps = scan(iface, ssid, namen, r["bssid"], alle=netze)
        r["netze"] = netze  # nicht in messungen.csv, sondern in netze.csv (siehe speichern)
        if aps:
            best = bester_ap_im_band(aps, r["freq_mhz"]) or aps[0]
            r.update(bester_ap=best["ap"], bester_ap_signal=best.get("signal_dbm"),
                     bester_ap_freq=best.get("freq_mhz"))
    if stop.is_set():
        raise Abgebrochen()
    return r, samples, aps


def last_zaehler(vor, nach):
    """WLAN-Zähler über den iperf-Upload. Leer bei Roaming oder Zählerrücksetzung."""
    if not vor or not nach or vor.get("bssid") != nach.get("bssid"):
        return {}
    d = {}
    for k in ("tx_packets", "tx_retries", "tx_failed"):
        a, b = vor.get(k), nach.get(k)
        if a is None or b is None or b < a:
            return {}
        d[k] = int(b - a)
    if not d["tx_packets"]:
        return {}
    return {"last_tx_packets": d["tx_packets"], "last_tx_retries": d["tx_retries"],
            "last_tx_failed": d["tx_failed"],
            "last_retry_pct": round(100 * d["tx_retries"] / d["tx_packets"], 1)}


def speichern(r, samples, aps):
    append_csv(MESSUNGEN, MESS_FELDER, [r])
    append_csv(SAMPLES, SAMPLE_FELDER, samples)
    append_csv(SCANS, SCAN_FELDER, [{**a, "id": r["id"], "ort": r["ort"]} for a in aps])
    if r.get("netze"):
        append_csv(NETZE, NETZ_FELDER, [{**n, "id": r["id"], "ort": r["ort"]} for n in r["netze"]])


def bester_ap_im_band(aps, freq):
    """Stärkster AP im selben Band wie `freq` (2,4 GHz reicht weiter als 5 GHz –
    ein bandübergreifender Vergleich wäre kein Hinweis auf einen klebenden Client)."""
    b = band(num(freq))
    kandidaten = [a for a in aps if band(num(a.get("freq_mhz"))) == b and num(a.get("signal_dbm")) is not None]
    return max(kandidaten, key=lambda a: num(a["signal_dbm"]), default=None)


def klebt(aps):
    """-> (Name des stärkeren AP, Differenz in dB), falls ein anderer AP im selben Band
    mind. 8 dB stärker ist als der verbundene, sonst None."""
    verb = next((a for a in aps if a.get("verbunden")), None)
    if not verb or num(verb.get("signal_dbm")) is None:
        return None
    best = bester_ap_im_band(aps, verb.get("freq_mhz"))
    if best is None or best is verb:
        return None
    diff = num(best["signal_dbm"]) - num(verb["signal_dbm"])
    return (best["ap"], diff) if diff >= 8 else None


def klebe_warnung(aps):
    k = klebt(aps)
    return f"{k[0]} wäre {k[1]:.0f} dB stärker – Client klebt am aktuellen AP" if k else None


def lade_scan(mid):
    return [a for a in read_csv(SCANS) if a.get("id") == mid]


# --------------------------------------------------------------------------- Auswertungen

def report_daten(szenario=None, alle=False):
    """Messungen (optional nur ein Szenario), ohne `alle` nur die letzte je (Szenario, Ort)."""
    rows = read_csv(MESSUNGEN)
    if szenario:
        rows = [r for r in rows if r.get("szenario") == szenario]
    scans = {}
    for a in read_csv(SCANS):
        scans.setdefault(a.get("id"), []).append(a)
    anz = {}
    for r in rows:
        k = (r.get("szenario", ""), r["ort"])
        anz[k] = anz.get(k, 0) + 1
    if not alle:
        letzte = {}
        for r in rows:
            letzte[(r.get("szenario", ""), r["ort"])] = r
        rows = list(letzte.values())
    orte, szf = lade_orte(), list(lade_szenarien())
    rows.sort(key=lambda r: (szf.index(r["szenario"]) if r.get("szenario") in szf else 999,
                             orte.index(r["ort"]) if r["ort"] in orte else 999,
                             r["zeit"]))
    for r in rows:
        h = []
        if int(r.get("roams") or 0):
            h.append(f"{r['roams']}× Roaming")
        k = klebt(scans.get(r["id"], []))  # aus dem gespeicherten Scan, nur innerhalb desselben Bands
        if k:
            h.append(f"{k[0]} +{k[1]:.0f} dB")
        if r.get("power_save") == "on":
            h.append("Stromsparmodus an")
        if r.get("netzteil") == "nein":
            h.append("Akku")
        if r.get("vpn"):
            h.append("VPN")
        if r.get("notiz"):
            h.append(r["notiz"])
        r["video"] = videocall_urteil(r)
        n = anz.get((r.get("szenario", ""), r["ort"]), 0)
        if not alle and n > 1:
            h.append(f"{n} Messungen")
        r["hinweis"] = ", ".join(h)
    return rows


def vergleich_daten(metrik="signal", szenarien=None):
    spalte, titel, hoch_gut, einheit, tol = METRIKEN[metrik]
    rows = read_csv(MESSUNGEN)
    alle_sz = list(lade_szenarien())
    vorhanden = {r.get("szenario") for r in rows if r.get("szenario")}
    szen = szenarien or [s for s in alle_sz if s in vorhanden] + sorted(vorhanden - set(alle_sz))
    werte = {}
    for r in rows:
        v = (videocall_urteil(r)["punkte"] if spalte == "video_score" else num(r.get(spalte)))
        if r.get("szenario") in szen and v is not None:
            werte.setdefault((r["ort"], r["szenario"]), []).append(v)
    orte = [o for o in lade_orte() if any((o, s) in werte for s in szen)]
    orte += sorted({o for o, _ in werte} - set(orte))
    # Median statt Mittelwert: robust gegen einzelne Ausreißer bei Wiederholungen
    mittel = {k: round(statistics.median(v), 1) for k, v in werte.items()}
    spanne = {k: (min(v), max(v)) for k, v in werte.items()}
    pick_best, pick_worst = (max, min) if hoch_gut else (min, max)

    def bewerte(vs, sp=None):
        """-> Liste von 'best'|'worst'|'' passend zu vs.

        Kein Sieger, wenn der Abstand unter der Toleranz liegt oder sich die Spannen
        wiederholter Messungen von Bestem und Schlechtestem überlappen (nicht belastbar)."""
        g = [v for v in vs if v is not None]
        if len(g) < 2:
            return [""] * len(vs)
        b, w = pick_best(g), pick_worst(g)
        if abs(b - w) < tol:
            return [""] * len(vs)
        if sp:
            sb, sw = sp[vs.index(b)], sp[vs.index(w)]
            if sb and sw and sb[0] <= sw[1] and sw[0] <= sb[1]:
                return ["unsicher" if v in (b, w) else "" for v in vs]
        return ["best" if v == b else "worst" if v == w else "" for v in vs]

    zeilen, siege = [], {s: 0 for s in szen}
    for o in orte:
        vs = [mittel.get((o, s)) for s in szen]
        sp = [spanne.get((o, s)) if len(werte.get((o, s), [])) > 1 else None for s in szen]
        mark = bewerte(vs, sp)
        for s, m in zip(szen, mark):
            if m == "best":
                siege[s] += 1
        zeilen.append({"ort": o, "werte": vs, "n": [len(werte.get((o, s), [])) for s in szen], "mark": mark,
                       "spanne": sp})

    gemeinsam = [o for o in orte if all((o, s) in mittel for s in szen)]
    zusammenfassung = []
    if gemeinsam and szen:
        for label, fn in (("Ø gemeinsame Orte", statistics.mean), ("schlechtester Ort", pick_worst)):
            vs = [round(fn([mittel[(o, s)] for o in gemeinsam]), 1) for s in szen]
            zusammenfassung.append({"ort": label, "werte": vs, "n": [1] * len(szen), "mark": bewerte(vs),
                                    "spanne": [None] * len(szen)})
    fehlend = {s: [o for o in orte if (o, s) not in mittel] for s in szen}
    return {"titel": titel, "einheit": einheit, "hoch_gut": hoch_gut, "toleranz": tol, "szenarien": szen,
            "zeilen": zeilen, "zusammenfassung": zusammenfassung, "siege": siege,
            "fehlend": {s: o for s, o in fehlend.items() if o}, "beschreibungen": lade_szenarien()}
