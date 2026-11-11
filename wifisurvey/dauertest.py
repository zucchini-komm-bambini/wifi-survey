"""Dauertest: minutenlang Videostrom und Ping an einem Platz, um seltene Aussetzer zu finden.

Eine 30-s-Messung erwischt ein Ereignis, das einmal in 10 Minuten auftritt, nur mit ~5 %
Wahrscheinlichkeit (DFS-Kanalwechsel, Mikrowelle, Last beim Nachbarn). Der Dauertest läuft
so lange wie ein echter Call und wertet jede Sekunde einzeln aus.

Gleichzeitig laufen
- iperf3 UDP in beide Richtungen (--bidir) mit dem Videostrom aus core.UDP_MBIT,
  Empfangsrichtung sekundengenau live, Senderichtung sekundengenau aus der Server-Ausgabe am Ende,
- Ping alle 0,2 s zum Gateway,
- Abfrage der WLAN-Verbindung jede Sekunde (Signal, AP-Wechsel, Kanalwechsel, Abbrüche).
"""

import json
import math
import re
import signal
import statistics
import subprocess
import threading
import time
from datetime import datetime

from . import ansage
from . import core as c

DAUERTESTS = c.DATA / "dauertests.csv"
SEKUNDEN = c.DATA / "dauertest_sekunden.csv"

DAUER_FELDER = [
    "id", "zeit", "szenario", "ort", "notiz", "dauer_s", "geplant_s", "vorzeitig",
    "ssid", "ap", "bssid", "freq_mhz", "signal_avg", "signal_min",
    "ping_ziel", "ping_verlust_pct", "rtt_avg", "rtt_p99", "rtt_max",
    "udp_up_verlust_pct", "udp_down_verlust_pct", "udp_up_jitter_ms", "udp_down_jitter_ms",
    "gestoerte_s", "aussetzer", "laengster_aussetzer_s", "roams", "kanalwechsel", "ohne_verbindung_s",
    "urteil", "gruende",
    "netzteil", "power_save", "vpn", "bluetooth", "kanalbreite_mhz", "kanalmitte_mhz",
]
SEK_FELDER = ["id", "sek", "signal", "bssid", "freq_mhz", "ping_gesendet", "ping_verloren", "rtt_max",
              "down_verlust_pct", "down_jitter_ms", "down_pakete", "up_verlust_pct", "up_jitter_ms", "up_pakete",
              "gestoert"]

PING_INTERVALL = 0.2
# Eine Sekunde gilt als gestört, wenn ein Videocall dort sichtbar ruckeln würde:
GRENZE_VERLUST = 5        # % verlorene Videopakete in dieser Sekunde (je Richtung)
GRENZE_PAKETE = 0.8       # weniger als 80 % der erwarteten Videopakete kamen an (Stillstand)
GRENZE_PING_VERLOREN = 2  # von 5 Pings ohne Antwort
GRENZE_RTT = 300          # ms Ping-Spitze
ANSAGE_ALLE = 60          # Restzeit-Ansage jede Minute
PAKETE_JE_S = c.UDP_MBIT * 1e6 / 8 / c.UDP_LAENGE

_SERVER_RX = re.compile(r"\[RX-S\]\s+([\d.]+)-([\d.]+)\s+sec.*?([\d.]+)\s+ms\s+(\d+)/(\d+)")


def dauertest(szenario, ort, dauer_s, notiz="", iface=None, ping_ziel=None, iperf_host=None, countdown=0,
              on_sekunde=None, on_status=None, stop=None):
    """Führt einen Dauertest durch und gibt (ergebnis, sekunden) zurück, speichert nicht.

    stop: threading.Event – beendet den Test vorzeitig; die bisherigen Daten werden ausgewertet
    (bei weniger als 60 s wirft er core.Abgebrochen). on_sekunde(sek, zwischenstand) jede Sekunde.
    """
    return ansage.mit_ansage(
        countdown, lambda ans: _dauertest(szenario, ort, dauer_s, notiz, iface, ping_ziel, iperf_host,
                                          on_sekunde, on_status, stop, ans),
        c.Abgebrochen, intervall=ANSAGE_ALLE, name="Dauertest")


def _dauertest(szenario, ort, dauer_s, notiz, iface, ping_ziel, iperf_host, on_sekunde, on_status, stop, ans):
    stop = stop or threading.Event()
    say = on_status or (lambda t: None)
    iface = iface or c.wlan_interface()
    if not iface:
        raise RuntimeError("Kein WLAN-Interface gefunden.")
    ssid, _ = c.link(iface)
    if not ssid:
        raise RuntimeError("Nicht mit einem WLAN verbunden.")
    if iperf_host:
        say("Prüfe iperf3-Server …")
        fehler = c.iperf_pruefen(iperf_host)
        if fehler:
            raise RuntimeError(fehler + " Feld leeren, um nur mit Ping zu testen.")
    ziel = ping_ziel or c.gateway(iface)
    if not ziel and not iperf_host:
        raise RuntimeError("Weder Ping-Ziel noch iperf3-Server – nichts zu messen.")
    vorab, vorab_warn = c.vorab_check(iface)
    for t in vorab_warn:
        say("⚠ " + t)
    if ans:
        ans.runterzaehlen(stop, say, c.Abgebrochen)
        ans.starten(dauer_s)

    mid = "D" + datetime.now().strftime("%Y%m%d-%H%M%S")
    pings, ping_info = {}, {}       # sek -> [rtt, …]; "gesendet" aus der Ping-Statistik
    down, iperf_info = {}, {}       # sek -> (verlust %, jitter ms, pakete); "server", "fehler"
    procs = []

    if ziel:
        ping = subprocess.Popen(["ping", "-n", "-i", str(PING_INTERVALL), "-w", str(dauer_s), ziel],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=c.ENV)
        procs.append(ping)
        threading.Thread(target=_lies_ping, args=(ping, pings, ping_info), daemon=True).start()
    if iperf_host:
        host, port = c.iperf_ziel(iperf_host)
        ip = subprocess.Popen(["iperf3", "-c", host, "-p", str(port), "-u", "-b", f"{c.UDP_MBIT}M",
                               "-l", str(c.UDP_LAENGE), "--bidir", "-t", str(dauer_s), "-i", "1",
                               "--json-stream", "--get-server-output"],
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=c.ENV)
        procs.append(ip)
        threading.Thread(target=_lies_iperf, args=(ip, down, iperf_info), daemon=True).start()

    say(f"Dauertest {dauer_s // 60} min {dauer_s % 60:02d} s läuft …")
    namen = c.lade_aps()
    verbindung = []                 # je Sekunde: (signal, bssid, freq) oder None
    t0 = time.time()
    vorzeitig = False
    while (sek := int(time.time() - t0)) < dauer_s:
        if stop.is_set():
            vorzeitig = True
            break
        st = c.station(iface)
        _, f = c.link(iface)
        while len(verbindung) <= sek:
            verbindung.append((st.get("signal"), st.get("bssid"), f) if st else None)
        if on_sekunde:
            zw = _auswerten(verbindung, pings, down, {}, max(0, sek - 2), ping_info, bool(ziel), bool(iperf_host))
            on_sekunde(sek, {**zw["zusammenfassung"], "signal": st.get("signal") if st else None,
                             "ap": c.ap_name(st["bssid"], namen) if st else None})
        nxt = t0 + sek + 1
        while time.time() < nxt and not stop.is_set():
            time.sleep(0.05)

    gelaufen = min(int(time.time() - t0), dauer_s)
    say("Werte aus …")
    for p in procs:  # vorzeitig: SIGINT, damit ping noch seine Statistik ausgibt
        if vorzeitig and p.poll() is None:
            p.send_signal(signal.SIGINT)
    for p in procs:
        try:
            p.wait(timeout=20)
        except subprocess.TimeoutExpired:
            p.kill()
    time.sleep(0.3)  # Leser-Threads die letzten Zeilen verarbeiten lassen
    if vorzeitig and gelaufen < 60:
        raise c.Abgebrochen()

    up = _server_intervalle(iperf_info.get("server", ""))
    erg = _auswerten(verbindung, pings, down, up, gelaufen, ping_info, bool(ziel), bool(iperf_host))
    z = erg["zusammenfassung"]
    letzte = next((v for v in reversed(verbindung) if v), None)
    sig = [v[0] for v in verbindung if v and v[0] is not None]
    r = {"id": mid, "zeit": datetime.now().isoformat(timespec="seconds"), "szenario": szenario, "ort": ort,
         "notiz": notiz, "dauer_s": gelaufen, "geplant_s": dauer_s, "vorzeitig": "ja" if vorzeitig else "",
         "ssid": ssid, "bssid": letzte[1] if letzte else None, "ap": c.ap_name(letzte[1], namen) if letzte else "",
         "freq_mhz": letzte[2] if letzte else None,
         "signal_avg": c.avg(sig), "signal_min": min(sig) if sig else None, "ping_ziel": ziel,
         **{k: z.get(k) for k in DAUER_FELDER if k in z}, **vorab, "vorab_warnungen": vorab_warn}
    if iperf_host and not up and not vorzeitig:
        r["iperf_fehler"] = iperf_info.get("fehler") or "Keine Werte für die Senderichtung vom iperf3-Server."
    u = urteil(r)
    r["urteil"], r["gruende"] = u["stufe"], "; ".join(u["gruende"])
    sekunden = [{**s, "id": mid} for s in erg["sekunden"]]
    return r, sekunden


# --------------------------------------------------------------------------- Leser

def _lies_ping(proc, pings, info):
    for l in proc.stdout:
        m = re.search(r"icmp_seq=(\d+).*time=([\d.]+) ms", l)
        if m:
            sek = int((int(m.group(1)) - 1) * PING_INTERVALL + 1e-6)
            pings.setdefault(sek, []).append(float(m.group(2)))
            continue
        m = re.search(r"(\d+) packets transmitted", l)
        if m:
            info["gesendet"] = int(m.group(1))


def _lies_iperf(proc, down, info):
    for l in proc.stdout:
        try:
            j = json.loads(l)
        except ValueError:
            continue
        ev, d = j.get("event"), j.get("data")
        if ev == "interval":
            s = (d or {}).get("sum_bidir_reverse") or {}  # Laptop empfängt
            if s.get("start") is not None and s.get("packets") is not None:
                down[int(round(s["start"]))] = (s.get("lost_percent") or 0, s.get("jitter_ms"),
                                                (s.get("packets") or 0) - (s.get("lost_packets") or 0))
        elif ev == "server_output_text":
            info["server"] = d or ""
        elif ev == "error":
            info["fehler"] = f"iperf3: {d}"


def _server_intervalle(text):
    """Sekundenwerte der Senderichtung (Server empfängt) aus der Server-Textausgabe."""
    up = {}
    for m in _SERVER_RX.finditer(text):
        a, b = float(m.group(1)), float(m.group(2))
        if b - a > 1.5:  # Gesamtzeile
            continue
        verloren, gesamt = int(m.group(4)), int(m.group(5))
        up[int(round(a))] = (round(100 * verloren / gesamt, 2) if gesamt else 100.0, float(m.group(3)),
                             gesamt - verloren)
    return up


# --------------------------------------------------------------------------- Auswertung

def _auswerten(verbindung, pings, down, up, bis, ping_info, mit_ping, mit_iperf):
    """Sekunden 0 … bis-1 auswerten. -> {"sekunden": [...], "zusammenfassung": {...}}"""
    gesendet_gesamt = ping_info.get("gesendet")
    mit_iperf = mit_iperf and bool(down)  # iperf lieferte gar nichts -> Fehler, keine "Störung"
    je_s = round(1 / PING_INTERVALL)
    sekunden, rtts = [], []
    for s in range(bis):
        v = verbindung[s] if s < len(verbindung) else None
        rtt = pings.get(s, [])
        rtts += rtt
        if mit_ping:
            gesendet = je_s if gesendet_gesamt is None else max(0, min(je_s, gesendet_gesamt - s * je_s))
            verloren = max(0, gesendet - len(rtt))
        else:
            gesendet = verloren = None
        d, u = down.get(s), up.get(s)
        g = []
        if v is None:
            g.append("keine Verbindung")
        if verloren is not None and verloren >= GRENZE_PING_VERLOREN:
            g.append("Ping")
        if rtt and max(rtt) > GRENZE_RTT:
            g.append("RTT")
        if mit_iperf:
            for name, x in (("↓", d), ("↑", u)):
                if x is None:
                    continue
                if x[0] >= GRENZE_VERLUST or x[2] < GRENZE_PAKETE * PAKETE_JE_S:
                    g.append("Video " + name)
            if d is None and s < bis - 1:
                g.append("Video ↓")  # gar keine Werte = Stillstand
        sekunden.append({"sek": s, "signal": v[0] if v else None, "bssid": v[1] if v else None,
                         "freq_mhz": v[2] if v else None, "ping_gesendet": gesendet, "ping_verloren": verloren,
                         "rtt_max": max(rtt) if rtt else None,
                         "down_verlust_pct": d[0] if d else None, "down_jitter_ms": round(d[1], 2) if d and d[1] is not None else None,
                         "down_pakete": d[2] if d else None,
                         "up_verlust_pct": u[0] if u else None, "up_jitter_ms": u[1] if u else None,
                         "up_pakete": u[2] if u else None, "gestoert": ", ".join(g)})

    # Aussetzer = zusammenhängende gestörte Sekunden
    laeufe, lauf = [], 0
    for s in sekunden:
        if s["gestoert"]:
            lauf += 1
        elif lauf:
            laeufe.append(lauf)
            lauf = 0
    if lauf:
        laeufe.append(lauf)
    verb = [s for s in sekunden if s["bssid"]]
    roams = sum(1 for a, b in zip(verb, verb[1:]) if a["bssid"] != b["bssid"])
    kanalwechsel = sum(1 for a, b in zip(verb, verb[1:])
                       if a["bssid"] == b["bssid"] and a["freq_mhz"] and b["freq_mhz"] and a["freq_mhz"] != b["freq_mhz"])

    def verlust(spalte, pakete):
        xs = [(s[spalte], s[pakete]) for s in sekunden if s[spalte] is not None]
        if not xs:
            return None
        # gewichtet: verlorene / (empfangene + verlorene) über alle Sekunden
        verl = sum(p * v / (100 - v) if v < 100 else PAKETE_JE_S for v, p in xs)
        emp = sum(p for _, p in xs)
        return round(100 * verl / (verl + emp), 2) if verl + emp else None

    def jitter(spalte):
        xs = [s[spalte] for s in sekunden if s[spalte] is not None]
        return round(statistics.median(xs), 2) if xs else None

    pg = sum(s["ping_gesendet"] or 0 for s in sekunden)
    pv = sum(s["ping_verloren"] or 0 for s in sekunden)
    rtts.sort()
    z = {"gestoerte_s": sum(1 for s in sekunden if s["gestoert"]), "aussetzer": len(laeufe),
         "laengster_aussetzer_s": max(laeufe, default=0), "roams": roams, "kanalwechsel": kanalwechsel,
         "ohne_verbindung_s": sum(1 for s in sekunden if "keine Verbindung" in s["gestoert"]),
         "ping_verlust_pct": round(100 * pv / pg, 2) if pg else None,
         "rtt_avg": round(statistics.mean(rtts), 1) if rtts else None,
         "rtt_p99": rtts[min(len(rtts) - 1, math.ceil(0.99 * len(rtts)) - 1)] if rtts else None,
         "rtt_max": rtts[-1] if rtts else None,
         "udp_down_verlust_pct": verlust("down_verlust_pct", "down_pakete"),
         "udp_up_verlust_pct": verlust("up_verlust_pct", "up_pakete"),
         "udp_down_jitter_ms": jitter("down_jitter_ms"), "udp_up_jitter_ms": jitter("up_jitter_ms"),
         "sekunden": bis}
    return {"sekunden": sekunden, "zusammenfassung": z}


def urteil(r):
    """Videocall-Eignung über die ganze Testdauer. -> wie core.videocall_urteil."""
    n = c.num
    dauer = n(r.get("dauer_s")) or 1
    aussetzer, laengster = int(n(r.get("aussetzer")) or 0), int(n(r.get("laengster_aussetzer_s")) or 0)
    je_10min = aussetzer * 600 / dauer
    gruende, punkte = [], 2
    if int(n(r.get("ohne_verbindung_s")) or 0):
        gruende.append(f"{int(n(r['ohne_verbindung_s']))} s ohne Verbindung")
        punkte = 0
    if aussetzer:
        gruende.append(f"{aussetzer} Aussetzer (≈{je_10min:.1f} je 10 min), längster {laengster} s")
        punkte = min(punkte, 0 if laengster >= 5 or je_10min > 3 else 1)
    verl = max((v for v in (n(r.get("udp_up_verlust_pct")), n(r.get("udp_down_verlust_pct")),
                            None if r.get("udp_down_verlust_pct") not in (None, "") else n(r.get("ping_verlust_pct")))
                if v is not None), default=None)
    if verl is not None and verl >= 0.5:
        gruende.append(f"Verlust {verl:g} %")
        punkte = min(punkte, 0 if verl >= 2 else 1)
    j = max((v for v in (n(r.get("udp_up_jitter_ms")), n(r.get("udp_down_jitter_ms"))) if v is not None), default=None)
    if j is not None and j >= 3:
        gruende.append(f"Jitter {j:g} ms")
        punkte = min(punkte, 0 if j >= 10 else 1)
    for k, text in (("roams", "AP-Wechsel"), ("kanalwechsel", "Kanalwechsel")):
        if int(n(r.get(k)) or 0):
            gruende.append(f"{int(n(r[k]))}× {text}")
            punkte = min(punkte, 1)
    return {"stufe": c.STUFEN[punkte], "punkte": punkte, "gruende": gruende,
            "basis": "Dauertest" + ("" if n(r.get("udp_down_verlust_pct")) is not None else " (nur Ping)")}


# --------------------------------------------------------------------------- Daten

def speichern(r, sekunden):
    c.append_csv(DAUERTESTS, DAUER_FELDER, [r])
    c.append_csv(SEKUNDEN, SEK_FELDER, sekunden)


def laden(szenario=None):
    rows = c.read_csv(DAUERTESTS)
    return [r for r in rows if not szenario or r.get("szenario") == szenario]


def loeschen(did):
    for path, felder in ((DAUERTESTS, DAUER_FELDER), (SEKUNDEN, SEK_FELDER)):
        rows = c.read_csv(path)
        rest = [r for r in rows if r.get("id") != did]
        if len(rest) != len(rows):
            c.schreibe_csv(path, felder, rest)
