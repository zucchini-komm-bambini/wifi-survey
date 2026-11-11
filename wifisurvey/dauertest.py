"""Dauertest: minutenlang Videostrom und Ping an einem Platz, um seltene Aussetzer zu finden.

Eine 30-s-Messung erwischt ein Ereignis, das einmal in 10 Minuten auftritt, nur mit ~5 %
Wahrscheinlichkeit (DFS-Kanalwechsel, Mikrowelle, Last beim Nachbarn). Der Dauertest läuft
so lange wie ein echter Call und wertet jede Sekunde einzeln aus.

Gleichzeitig laufen
- iperf3 UDP in beide Richtungen (--bidir) mit dem Videostrom aus core.UDP_MBIT, in Abschnitten
  von ABSCHNITT_S Sekunden: Empfangsrichtung sekundengenau live, Senderichtung sekundengenau aus
  der Server-Ausgabe am Ende jedes Abschnitts. Reißt der Strom ab, wird er neu gestartet.
- Ping alle 0,2 s zum Gateway,
- Abfrage der WLAN-Verbindung jede Sekunde (Signal, AP-Wechsel, Kanalwechsel, Abbrüche).

Der Ping ist die Gegenprobe zum Videostrom: Kommt vom Videostrom nichts an, während alle Pings
derselben Sekunde durchkommen, liegt es nicht am WLAN (iperf3 oder der Server hängt). Das zählt
als Messfehler und nicht als Aussetzer.
"""

import json
import math
import re
import shutil
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
    "messfehler_s", "video_down_s", "video_up_s", "iperf_fehler", "bewertung",
]
SEK_FELDER = ["id", "sek", "signal", "bssid", "freq_mhz", "ping_gesendet", "ping_verloren", "rtt_max",
              "down_verlust_pct", "down_jitter_ms", "down_pakete", "up_verlust_pct", "up_jitter_ms", "up_pakete",
              "gestoert", "messfehler"]

BEWERTUNG = "2"           # Stand der Auswerteregeln; ältere gespeicherte Tests werden neu bewertet
PING_INTERVALL = 0.2
# Eine Sekunde gilt als gestört, wenn ein Videocall dort sichtbar ruckeln würde:
GRENZE_VERLUST = 5        # % verlorene Videopakete in dieser Sekunde (je Richtung)
GRENZE_PAKETE = 0.8       # weniger als 80 % der erwarteten Videopakete kamen an (Stillstand)
GRENZE_PING_VERLOREN = 2  # Pings ohne Antwort in dieser Sekunde (von 5)
GRENZE_RTT = 300          # ms Ping-Spitze
ANSAGE_ALLE = 60          # Restzeit-Ansage jede Minute
ABSCHNITT_S = 60          # Länge eines iperf3-Laufs; danach liegen die Werte der Senderichtung vor
STILLSTAND_S = 3          # so viele Sekunden ohne ein Videopaket -> iperf3 neu starten
PAKETE_JE_S = c.UDP_MBIT * 1e6 / 8 / c.UDP_LAENGE
MESSFEHLER = "Videostrom abgerissen"

_SERVER_RX = re.compile(r"\[RX-S\]\s+([\d.]+)-([\d.]+)\s+sec.*?([\d.]+)\s+ms\s+(\d+)/(\d+)")
_PING_RX = re.compile(r"\[(\d+\.\d+)\].*icmp_seq=(\d+).*time=([\d.]+) ms")


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
    pings, ping_info = {}, {}       # seq -> (Sendezeit seit t0, rtt); "gesendet" aus der Ping-Statistik
    down, up, iperf_info = {}, {}, {}  # sek -> (verlust %, jitter ms, pakete); "fehler": [(sek, text)]
    ende = threading.Event()        # beendet den Videostrom
    t0 = time.time()
    ping = iperf_thread = None

    if ziel:
        ping = subprocess.Popen(["ping", "-n", "-D", "-i", str(PING_INTERVALL), "-w", str(dauer_s), ziel],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=c.ENV)
        threading.Thread(target=_lies_ping, args=(ping, t0, pings, ping_info), daemon=True).start()
    if iperf_host:
        host, port = c.iperf_ziel(iperf_host)
        iperf_thread = threading.Thread(target=_iperf_schleife, daemon=True,
                                        args=(host, port, t0, dauer_s, ende, down, up, iperf_info))
        iperf_thread.start()

    say(f"Dauertest {dauer_s // 60} min {dauer_s % 60:02d} s läuft …")
    namen = c.lade_aps()
    verbindung = []                 # je Sekunde: (signal, bssid, freq) oder None
    vorzeitig = False
    gemeldet, zuletzt_gemeldet = 0, -30
    while (sek := int(time.time() - t0)) < dauer_s:
        if stop.is_set():
            vorzeitig = True
            break
        st = c.station(iface)
        _, f = c.link(iface)
        while len(verbindung) <= sek:
            verbindung.append((st.get("signal"), st.get("bssid"), f) if st else None)
        for wann, text in iperf_info.get("fehler", [])[gemeldet:]:
            if wann - zuletzt_gemeldet >= 30:  # bei einer längeren Störung nicht jeden Fehlversuch melden
                say(f"⚠ {wann // 60}:{wann % 60:02d} {text} – starte den Videostrom neu")
                zuletzt_gemeldet = wann
            gemeldet += 1
        if on_sekunde:
            zw = _auswerten(verbindung, pings, down, up, max(0, sek - 2), ping_info, bool(ziel),
                            jetzt=time.time() - t0)
            on_sekunde(sek, {**zw["zusammenfassung"], "signal": st.get("signal") if st else None,
                             "ap": c.ap_name(st["bssid"], namen) if st else None})
        nxt = t0 + sek + 1
        while time.time() < nxt and not stop.is_set():
            time.sleep(0.05)

    gelaufen = min(int(time.time() - t0), dauer_s)
    say("Werte aus …")
    if vorzeitig:
        ende.set()
        if ping and ping.poll() is None:  # SIGINT, damit ping noch seine Statistik ausgibt
            ping.send_signal(signal.SIGINT)
    if iperf_thread:
        iperf_thread.join(timeout=20)  # der letzte Abschnitt holt noch die Server-Werte
        ende.set()
        iperf_thread.join(timeout=10)
    if ping:
        try:
            ping.wait(timeout=20)
        except subprocess.TimeoutExpired:
            ping.kill()
    time.sleep(0.3)  # Leser-Threads die letzten Zeilen verarbeiten lassen
    if vorzeitig and gelaufen < 60:
        raise c.Abgebrochen()

    erg = _auswerten(verbindung, pings, down, up, gelaufen, ping_info, bool(ziel))
    z = erg["zusammenfassung"]
    letzte = next((v for v in reversed(verbindung) if v), None)
    sig = [v[0] for v in verbindung if v and v[0] is not None]
    r = {"id": mid, "zeit": datetime.now().isoformat(timespec="seconds"), "szenario": szenario, "ort": ort,
         "notiz": notiz, "dauer_s": gelaufen, "geplant_s": dauer_s, "vorzeitig": "ja" if vorzeitig else "",
         "ssid": ssid, "bssid": letzte[1] if letzte else None, "ap": c.ap_name(letzte[1], namen) if letzte else "",
         "freq_mhz": letzte[2] if letzte else None,
         "signal_avg": c.avg(sig), "signal_min": min(sig) if sig else None, "ping_ziel": ziel,
         **{k: z.get(k) for k in DAUER_FELDER if k in z}, **vorab, "vorab_warnungen": vorab_warn,
         "bewertung": BEWERTUNG}
    if iperf_host:
        r["iperf_fehler"] = _fehlertext(iperf_info.get("fehler", []), bool(down), bool(up))
    u = urteil(r)
    r["urteil"], r["gruende"] = u["stufe"], "; ".join(u["gruende"])
    sekunden = [{**s, "id": mid} for s in erg["sekunden"]]
    return r, sekunden


def _fehlertext(fehler, hat_down, hat_up):
    teile = []
    if fehler:
        wann, text = fehler[0]
        teile.append(f"Videostrom {len(fehler)}× neu gestartet (zuerst bei {wann // 60}:{wann % 60:02d}: {text})")
    if not hat_down:
        teile.append("keine Werte vom Videostrom")
    elif not hat_up:
        teile.append("keine Werte für die Senderichtung vom iperf3-Server")
    return "; ".join(teile)


# --------------------------------------------------------------------------- Videostrom

def _iperf_schleife(host, port, t0, dauer_s, ende, down, up, info):
    """Hält den Videostrom bis zum Testende am Laufen, Abschnitt für Abschnitt."""
    fehler_in_folge = 0
    while not ende.is_set():
        rest = dauer_s - (time.time() - t0)
        if rest < 3:
            break
        # kein Mini-Abschnitt am Schluss: der letzte läuft bis zum Ende durch
        laenge = int(rest) if rest < ABSCHNITT_S + 15 else ABSCHNITT_S
        fehler = _iperf_abschnitt(host, port, laenge, t0, ende, down, up)
        if ende.is_set():
            break
        if fehler:
            info.setdefault("fehler", []).append((int(time.time() - t0), fehler))
            fehler_in_folge += 1
            ende.wait(min(fehler_in_folge, 5))  # dem Server Zeit geben, den alten Test abzuschließen
        else:
            fehler_in_folge = 0


def _iperf_abschnitt(host, port, laenge, t0, ende, down, up):
    """Ein iperf3-Lauf. Trägt die Sekundenwerte in down/up ein. -> Fehlertext oder None"""
    try:
        p = subprocess.Popen(["iperf3", "-c", host, "-p", str(port), "-u", "-b", f"{c.UDP_MBIT}M",
                              "-l", str(c.UDP_LAENGE), "--bidir", "-t", str(laenge), "-i", "1",
                              "--json-stream", "--get-server-output"],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=c.ENV)
    except FileNotFoundError:
        ende.set()
        return "iperf3 ist nicht installiert."
    zustand = {"offset": None, "null": 0, "zeile": time.time(), "server": "", "fehler": None}
    leser = threading.Thread(target=_lies_iperf, args=(p.stdout, t0, down, zustand), daemon=True)
    leser.start()
    grund = None
    while p.poll() is None:
        if ende.is_set():
            p.send_signal(signal.SIGINT)
            break
        if zustand["null"] >= STILLSTAND_S:
            grund = "Videostrom lieferte keine Pakete mehr"
        elif time.time() - zustand["zeile"] > STILLSTAND_S + 2:
            grund = "iperf3 reagierte nicht mehr"
        if grund:
            p.kill()
            break
        time.sleep(0.1)
    try:
        p.wait(timeout=10)
    except subprocess.TimeoutExpired:
        p.kill()
    leser.join(timeout=2)
    if zustand["offset"] is not None:
        for sek, werte in _server_intervalle(zustand["server"]).items():
            up[int(round(zustand["offset"])) + sek] = werte
    return grund or zustand["fehler"]


def _lies_iperf(zeilen, t0, down, zustand):
    """Liest die --json-stream-Ausgabe eines Abschnitts. t0: Testbeginn (time.time())."""
    for l in zeilen:
        zustand["zeile"] = time.time()
        try:
            j = json.loads(l)
        except ValueError:
            if l.strip():
                zustand["fehler"] = l.strip()[:200]
            continue
        ev, d = j.get("event"), j.get("data")
        if ev == "interval":
            s = (d or {}).get("sum_bidir_reverse") or {}  # Laptop empfängt
            if s.get("start") is None or s.get("packets") is None:
                continue
            if zustand["offset"] is None:  # Lage dieses Abschnitts auf der Zeitachse des Tests
                zustand["offset"] = time.time() - t0 - (s.get("end") or 0)
            if (s.get("seconds") or 1) < 0.9:
                continue  # Restintervall am Ende, zu kurz für die Paketzahl einer Sekunde
            empfangen = (s.get("packets") or 0) - (s.get("lost_packets") or 0)
            zustand["null"] = zustand["null"] + 1 if empfangen <= 0 else 0
            down[int(round(zustand["offset"] + s["start"]))] = (s.get("lost_percent") or 0, s.get("jitter_ms"),
                                                                empfangen)
        elif ev == "server_output_text":
            zustand["server"] = d or ""
        elif ev == "error":
            zustand["fehler"] = f"iperf3: {d}"


def _server_intervalle(text):
    """Sekundenwerte der Senderichtung (Server empfängt) aus der Server-Textausgabe.
    -> {Sekunde seit Abschnittsbeginn: (verlust %, jitter ms, pakete)}"""
    up = {}
    for m in _SERVER_RX.finditer(text or ""):
        a, b = float(m.group(1)), float(m.group(2))
        if not 0.9 <= b - a <= 1.5:  # Gesamtzeile oder Restintervall
            continue
        verloren, gesamt = int(m.group(4)), int(m.group(5))
        up[int(round(a))] = (round(100 * verloren / gesamt, 2) if gesamt else 100.0, float(m.group(3)),
                             gesamt - verloren)
    return up


# --------------------------------------------------------------------------- Ping

def _lies_ping(proc, t0, pings, info):
    """pings[seq] = (Sendezeit in s seit t0, rtt in ms) – aus den Zeitstempeln von ping -D."""
    for l in proc.stdout:
        m = _PING_RX.search(l)
        if m:
            rtt = float(m.group(3))
            pings[int(m.group(2))] = (float(m.group(1)) - rtt / 1000 - t0, rtt)
            continue
        m = re.search(r"(\d+) packets transmitted", l)
        if m:
            info["gesendet"] = int(m.group(1))


def _ping_sekunden(pings, gesendet=None, jetzt=None):
    """Ordnet jeden Ping (auch die verlorenen) der Sekunde zu, in der er gesendet wurde.

    ping zählt nur mit, es stoppt die Zeit nicht: Über Minuten läuft die Folge icmp_seq × Intervall
    der Uhr davon. Deshalb zählen die Zeitstempel der Antworten, und verlorene Pings werden
    zwischen ihren Nachbarn eingeordnet.
    gesendet: Anzahl laut Ping-Statistik (am Ende); jetzt: Sekunden seit t0 (während des Tests).
    -> {sek: [gesendet, verloren, [rtt, …]]}
    """
    pings = dict(pings)  # der Leser-Thread schreibt weiter
    seqs = sorted(pings)
    if seqs:
        letzter = seqs[-1]
        n = gesendet or letzter
        if not gesendet and jetzt is not None:  # seit der letzten Antwort überfällige Pings
            n += max(0, int((jetzt - 1 - pings[letzter][0]) / PING_INTERVALL))
    else:
        n = gesendet or (int(max(0, jetzt - 1) / PING_INTERVALL) if jetzt else 0)
    je_s = {}
    davor, i = None, 0  # letzte Antwort vor seq; Index der nächsten Antwort
    for seq in range(1, n + 1):
        if seq in pings:
            t, rtt = pings[seq]
            davor = seq
        else:
            rtt = None
            while i < len(seqs) and seqs[i] < seq:
                i += 1
            danach = seqs[i] if i < len(seqs) else None
            if davor is not None and danach is not None:
                t0_, t1_ = pings[davor][0], pings[danach][0]
                t = t0_ + (seq - davor) * (t1_ - t0_) / (danach - davor)
            elif davor is not None:
                t = pings[davor][0] + (seq - davor) * PING_INTERVALL
            elif danach is not None:
                t = pings[danach][0] - (danach - seq) * PING_INTERVALL
            else:
                t = (seq - 1) * PING_INTERVALL
        e = je_s.setdefault(int(math.floor(t)), [0, 0, []])
        e[0] += 1
        if rtt is None:
            e[1] += 1
        else:
            e[2].append(rtt)
    return je_s


# --------------------------------------------------------------------------- Auswertung

def _auswerten(verbindung, pings, down, up, bis, ping_info, mit_ping, jetzt=None):
    """Sekunden 0 … bis-1 auswerten. -> {"sekunden": [...], "zusammenfassung": {...}}"""
    je_s = _ping_sekunden(pings, ping_info.get("gesendet"), jetzt) if mit_ping else {}
    sekunden, rtts = [], []
    for s in range(bis):
        v = verbindung[s] if s < len(verbindung) else None
        gesendet, verloren, rtt = je_s.get(s, [0, 0, []]) if mit_ping else (None, None, [])
        rtts += rtt
        d, u = down.get(s), up.get(s)
        sekunden.append({"sek": s, "signal": v[0] if v else None, "bssid": v[1] if v else None,
                         "freq_mhz": v[2] if v else None, "ping_gesendet": gesendet, "ping_verloren": verloren,
                         "rtt_max": max(rtt) if rtt else None,
                         "down_verlust_pct": d[0] if d else None,
                         "down_jitter_ms": round(d[1], 2) if d and d[1] is not None else None,
                         "down_pakete": d[2] if d else None,
                         "up_verlust_pct": u[0] if u else None, "up_jitter_ms": u[1] if u else None,
                         "up_pakete": u[2] if u else None})
    bewerten(sekunden)
    rtts.sort()
    z = zusammenfassen(sekunden)
    z.update({"rtt_avg": round(statistics.mean(rtts), 1) if rtts else None,
              "rtt_p99": rtts[min(len(rtts) - 1, math.ceil(0.99 * len(rtts)) - 1)] if rtts else None,
              "rtt_max": rtts[-1] if rtts else None})
    return {"sekunden": sekunden, "zusammenfassung": z}


def bewerten(sekunden):
    """Setzt je Sekunde "gestoert" (Ursachen, leer = in Ordnung) und "messfehler".

    Videostrom ohne ein einziges Paket bei gleichzeitig vollständigem Ping = Messfehler, kein Aussetzer.
    Die Sekunden direkt davor und danach (Strom läuft aus bzw. wieder an) gehören dazu.
    """
    n = len(sekunden)
    video = [[] for _ in range(n)]      # gestörte Richtungen
    leer = [False] * n                  # mindestens eine Richtung ganz ohne Pakete, keine mit Teilverlust
    ping_ok, ping_schlecht = [False] * n, [False] * n
    for i, s in enumerate(sekunden):
        g = []
        if not s["bssid"]:
            g.append("keine Verbindung")
        if s["ping_verloren"] is not None and s["ping_verloren"] >= GRENZE_PING_VERLOREN:
            g.append("Ping")
        if s["rtt_max"] is not None and s["rtt_max"] > GRENZE_RTT:
            g.append("RTT")
        ping_schlecht[i] = bool(g)
        ping_ok[i] = not g and (s["ping_gesendet"] or 0) >= 3 and s["ping_verloren"] == 0
        teil = False
        for name, pre in (("↓", "down"), ("↑", "up")):
            verl, pak = s[pre + "_verlust_pct"], s[pre + "_pakete"]
            if pak is None:
                continue  # in dieser Sekunde nicht gemessen (Pause zwischen zwei Abschnitten)
            if pak <= 0:
                video[i].append("Video " + name)
            elif (verl or 0) >= GRENZE_VERLUST or pak < GRENZE_PAKETE * PAKETE_JE_S:
                video[i].append("Video " + name)
                teil = True
        leer[i] = bool(video[i]) and not teil
        s["gestoert"], s["messfehler"] = g, ""
    mf = [leer[i] and ping_ok[i] for i in range(n)]
    for i in range(1, n):       # Abriss dauert an, auch wenn der Ping in dieser Sekunde nichts beweist
        if leer[i] and not ping_schlecht[i] and mf[i - 1]:
            mf[i] = True
    rand = [bool(video[i]) and not leer[i] and not ping_schlecht[i]
            and ((i > 0 and mf[i - 1]) or (i + 1 < n and mf[i + 1])) for i in range(n)]
    for i, s in enumerate(sekunden):
        if mf[i] or rand[i]:
            s["messfehler"] = MESSFEHLER
        else:
            s["gestoert"] += video[i]
        s["gestoert"] = ", ".join(s["gestoert"])


def zusammenfassen(sekunden):
    """Kennzahlen über bewertete Sekunden (ohne die RTT-Statistik, die alle Einzelpings braucht)."""
    laeufe, lauf = [], 0  # Aussetzer = zusammenhängende gestörte Sekunden
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
    gueltig = [s for s in sekunden if not s["messfehler"]]

    def verlust(spalte, pakete):
        xs = [(s[spalte], s[pakete]) for s in gueltig if s[spalte] is not None]
        if not xs:
            return None
        # gewichtet: verlorene / (empfangene + verlorene) über alle Sekunden
        verl = sum(p * v / (100 - v) if v < 100 else PAKETE_JE_S for v, p in xs)
        emp = sum(p for _, p in xs)
        return round(100 * verl / (verl + emp), 2) if verl + emp else None

    def jitter(spalte):
        xs = [s[spalte] for s in gueltig if s[spalte] is not None]
        return round(statistics.median(xs), 2) if xs else None

    pg = sum(s["ping_gesendet"] or 0 for s in sekunden)
    pv = sum(s["ping_verloren"] or 0 for s in sekunden)
    spitzen = [s["rtt_max"] for s in sekunden if s["rtt_max"] is not None]
    return {"gestoerte_s": sum(1 for s in sekunden if s["gestoert"]), "aussetzer": len(laeufe),
            "laengster_aussetzer_s": max(laeufe, default=0), "roams": roams, "kanalwechsel": kanalwechsel,
            "ohne_verbindung_s": sum(1 for s in sekunden if "keine Verbindung" in s["gestoert"]),
            "messfehler_s": sum(1 for s in sekunden if s["messfehler"]),
            "video_down_s": sum(1 for s in gueltig if s["down_pakete"] is not None),
            "video_up_s": sum(1 for s in gueltig if s["up_pakete"] is not None),
            "ping_verlust_pct": round(100 * pv / pg, 2) if pg else None,
            "rtt_max": max(spitzen, default=None),
            "udp_down_verlust_pct": verlust("down_verlust_pct", "down_pakete"),
            "udp_up_verlust_pct": verlust("up_verlust_pct", "up_pakete"),
            "udp_down_jitter_ms": jitter("down_jitter_ms"), "udp_up_jitter_ms": jitter("up_jitter_ms"),
            "sekunden": len(sekunden)}


def _mmss(s):
    s = int(s)
    return f"{s // 60}:{s % 60:02d}"


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
    hat_video = r.get("udp_down_verlust_pct") not in (None, "") or r.get("udp_up_verlust_pct") not in (None, "")
    verl = max((v for v in (n(r.get("udp_up_verlust_pct")), n(r.get("udp_down_verlust_pct")),
                            None if hat_video else n(r.get("ping_verlust_pct")))
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
    basis = "Dauertest"
    if not hat_video:
        basis += " (nur Ping)"
    else:  # sagen, wie viel der Zeit der Videostrom wirklich gemessen wurde – den Rest deckt nur der Ping ab
        for pfeil, k in (("↑", "video_up_s"), ("↓", "video_down_s")):
            if r.get(k) in (None, ""):
                continue
            gemessen = n(r.get(k)) or 0
            if not gemessen:
                basis += f", Video {pfeil} nicht gemessen"
            elif gemessen < 0.9 * dauer:
                basis += f", Video {pfeil} nur {_mmss(gemessen)} von {_mmss(dauer)} gemessen"
    return {"stufe": c.STUFEN[punkte], "punkte": punkte, "gruende": gruende, "basis": basis,
            "reserve_db": c.reserve_db(n(r.get("signal_avg")))}


# --------------------------------------------------------------------------- Daten

def speichern(r, sekunden):
    c.append_csv(DAUERTESTS, DAUER_FELDER, [r])
    c.append_csv(SEKUNDEN, SEK_FELDER, sekunden)


def laden(szenario=None):
    rows = c.read_csv(DAUERTESTS)
    if any(r.get("bewertung") != BEWERTUNG for r in rows):
        rows = _neu_bewerten(rows)
    return [r for r in rows if not szenario or r.get("szenario") == szenario]


def _neu_bewerten(rows):
    """Bewertet Tests mit älterem Regelstand aus den gespeicherten Sekundenwerten neu (einmalig).

    Die Messwerte bleiben, es ändern sich nur die abgeleiteten Angaben (gestört, Aussetzer, Urteil).
    Die alte Übersicht bleibt als dauertests.csv.bak liegen.
    """
    zahl = ("sek", "signal", "freq_mhz", "ping_gesendet", "ping_verloren", "rtt_max", "down_verlust_pct",
            "down_jitter_ms", "down_pakete", "up_verlust_pct", "up_jitter_ms", "up_pakete")
    alle = c.read_csv(SEKUNDEN)
    je_test = {}
    for s in alle:
        je_test.setdefault(s["id"], []).append(s)
    for r in rows:
        if r.get("bewertung") == BEWERTUNG:
            continue
        roh = je_test.get(r["id"], [])
        if roh:
            sek = [{**s, **{k: c.num(s.get(k)) for k in zahl}} for s in roh]
            bewerten(sek)
            z = zusammenfassen(sek)
            r.update({k: z[k] for k in DAUER_FELDER if k in z})
            for alt, neu in zip(roh, sek):
                alt["gestoert"], alt["messfehler"] = neu["gestoert"], neu["messfehler"]
        r["bewertung"] = BEWERTUNG
        u = urteil(r)
        r["urteil"], r["gruende"] = u["stufe"], "; ".join(u["gruende"])
    shutil.copy2(DAUERTESTS, DAUERTESTS.with_suffix(".csv.bak"))
    c.schreibe_csv(DAUERTESTS, DAUER_FELDER, rows)
    if alle:
        c.schreibe_csv(SEKUNDEN, SEK_FELDER, alle)
    return rows


def loeschen(did):
    for path, felder in ((DAUERTESTS, DAUER_FELDER), (SEKUNDEN, SEK_FELDER)):
        rows = c.read_csv(path)
        rest = [r for r in rows if r.get("id") != did]
        if len(rest) != len(rows):
            c.schreibe_csv(path, felder, rest)
