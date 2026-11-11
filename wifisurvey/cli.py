"""Kommandozeile für wifi-survey. Ohne Unterbefehl startet die TUI."""

import argparse
import os
import shutil
import subprocess
import sys
import threading

from . import __version__
from . import anzeige
from . import ansage
from . import core as c
from . import dauertest
from . import empfehlung
from . import umfeld

FARBEN = {"gut": "32", "mittel": "33", "schlecht": "31", "best": "1;32", "worst": "31", "": ""}


def farbe(text, stufe):
    code = FARBEN.get(stufe, stufe)
    return f"\033[{code}m{text}\033[0m" if code and sys.stdout.isatty() else str(text)


def f(v, suffix=""):
    return "–" if v in (None, "") else f"{v}{suffix}"


# --------------------------------------------------------------------------- Auswahl

def waehle(optionen, frage):
    if shutil.which("gum") and sys.stdin.isatty():
        r = subprocess.run(["gum", "choose", "--header", frage, *optionen], stdout=subprocess.PIPE, text=True)
        return r.stdout.strip() or None
    for i, o in enumerate(optionen, 1):
        print(f"  {i:2}) {o}")
    a = input(f"{frage} [Nummer]: ").strip()
    return optionen[int(a) - 1] if a.isdigit() and 0 < int(a) <= len(optionen) else None


def eingabe(frage, default=""):
    if shutil.which("gum") and sys.stdin.isatty():
        r = subprocess.run(["gum", "input", "--placeholder", frage, "--value", default],
                           stdout=subprocess.PIPE, text=True)
        return r.stdout.strip()
    return input(f"{frage}: ").strip() or default


def szenario_auswaehlen():
    sz, anz, akt = c.lade_szenarien(), c.anzahl_messungen(), c.aktives_szenario()
    NEU = "+ Neues Szenario …"
    namen = list(sz)
    labels = [("● " if n == akt else "  ") + n + (f" — {sz[n]}" if sz[n] else "")
              + f"  ({sum(v for (s, _), v in anz.items() if s == n)} Messungen)" for n in namen]
    wahl = waehle(labels + [NEU], "Welches Setup ist gerade aufgebaut?")
    if not wahl:
        return None
    if wahl == NEU:
        name = eingabe("Name des Szenarios (z.B. 'Repeater A im Flur')")
        if not name:
            return None
        name = c.szenario_speichern(name, eingabe("Beschreibung (Geräte, Standorte, Kabel/Funk …)"))
    else:
        name = namen[labels.index(wahl)]
    c.szenario_aktivieren(name)
    return name


def ort_auswaehlen(szenario):
    orte, anz = c.lade_orte(), c.anzahl_messungen()
    NEU = "+ Neuer Ort …"
    labels = [f"✓ {o}  ({anz[(szenario, o)]}×)" if (szenario, o) in anz else f"  {o}" for o in orte]
    wahl = waehle(labels + [NEU], f"[{szenario}] Wo stehst du gerade?")
    if not wahl:
        return None
    if wahl == NEU:
        ort = eingabe("Name des neuen Ortes")
        c.ort_hinzufuegen(ort)
        return ort or None
    return orte[labels.index(wahl)]


# --------------------------------------------------------------------------- Befehle

def cmd_messen(args):
    cfg = c.lade_config()
    szenario = args.szenario or c.aktives_szenario() or szenario_auswaehlen()
    if not szenario:
        sys.exit("Kein Szenario gewählt – erst 'wifi-survey szenario' ausführen.")
    if szenario not in c.lade_szenarien():
        c.szenario_speichern(szenario)
    ort = args.ort or ort_auswaehlen(szenario)
    if not ort:
        sys.exit("Abgebrochen.")
    notiz = args.notiz if args.notiz is not None else eingabe("Notiz (optional, z.B. 'Tür zu')")
    dauer = args.dauer or int(cfg["dauer"])
    print(f"\n🧪 Szenario: {szenario}\n📍 {ort} – Laptop ruhig halten …\n")

    def sample(sek, st, ap):
        if st:
            print(f"\r   {sek:3}s  {st['signal']:.0f} dBm  TX {st['tx_bitrate'] or 0:.0f} / "
                  f"RX {st['rx_bitrate'] or 0:.0f} Mbit/s  {ap:<22}", end="", flush=True)
        else:
            print(f"\r   {sek:3}s  ⚠ keine Verbindung{' ' * 40}", end="", flush=True)

    def status(t):
        if t.startswith("Start in"):  # Countdown in einer Zeile herunterzählen
            print(f"\r   {t}   ", end="", flush=True)
        else:
            print(f"\n   {t}", end="", flush=True)

    countdown = args.countdown if args.countdown is not None else int(cfg["countdown"])
    if countdown:
        h = ansage.hinweis()
        if h:
            print(farbe(f"   ⚠ {h}", "mittel"))
    try:
        r, samples, aps = c.messung(
            szenario, ort, notiz, dauer, iface=args.interface, ping_ziel=args.ping or cfg["ping_ziel"] or None,
            iperf_host=args.iperf or cfg["iperf_host"] or None, mit_scan=not args.kein_scan and cfg["scan"],
            mit_udp=not args.kein_udp and cfg["udp"], countdown=countdown,
            on_sample=sample, on_status=status)
    except c.Abgebrochen:
        sys.exit("\nAbgebrochen – nichts gespeichert.")
    except KeyboardInterrupt:
        sys.exit("\nAbgebrochen – nichts gespeichert.")
    except RuntimeError as e:
        sys.exit(farbe(f"\n✗ {e}", "schlecht"))
    print()
    c.speichern(r, samples, aps)
    zeige_messung(r, aps)


def zeige_messung(r, aps):
    txt, st = c.bewertung_signal(c.num(r.get("signal_avg")))
    print(f"\n── Ergebnis: {r['ort']}  [{r.get('szenario', '')}] ─────────────────────────────")
    print(f"  AP          {r['ap']}  ({c.kanal_name(c.num(r['freq_mhz']))})"
          + (f"  ⚠ {r['roams']}× AP gewechselt" if int(r.get('roams') or 0) else ""))
    print(f"  Signal      {farbe(f(r['signal_avg'], ' dBm'), st)} ({txt})  "
          f"min {f(r['signal_min'])} / max {f(r['signal_max'])}")
    print(f"  Bitrate     TX {f(r['tx_bitrate_avg'])}  RX {f(r['rx_bitrate_avg'])} Mbit/s")
    print(f"  Ping        Verlust {farbe(f(r.get('ping_verlust_pct'), ' %'), c.stufe_loss(c.num(r.get('ping_verlust_pct'))))}  "
          f"RTT {f(r.get('rtt_avg'))} ms (max {f(r.get('rtt_max'))})  Jitter {f(r.get('jitter_ms'))} ms")
    rq = c.num(r.get("retry_quote_pct"))
    print(f"  WLAN-Ebene  Retries {farbe(f(r.get('retry_quote_pct'), ' %'), c.stufe_retry(rq))}  "
          f"TX failed {f(r.get('tx_failed'))}  Beacon loss {f(r.get('beacon_loss'))}"
          + (f"  (nur {r['tx_packets']} Pakete – Retry-Quote unsicher)"
             if r.get("tx_packets") not in (None, "") and int(r["tx_packets"]) < 500 else ""))
    if r.get("last_retry_pct") not in (None, ""):
        print(f"  Unter Last  Retries {farbe(f(r['last_retry_pct'], ' %'), c.stufe_retry(c.num(r['last_retry_pct'])))}  "
              f"TX failed {f(r.get('last_tx_failed'))}  ({f(r.get('last_tx_packets'))} Pakete beim Upload)")
    if r.get("iperf_down_mbit") or r.get("iperf_up_mbit"):
        print(f"  iperf3      ↓ {f(r.get('iperf_down_mbit'))}  ↑ {f(r.get('iperf_up_mbit'))} Mbit/s")
    for richtung, pfeil in (("up", "↑"), ("down", "↓")):
        j, v = c.num(r.get(f"udp_{richtung}_jitter_ms")), c.num(r.get(f"udp_{richtung}_verlust_pct"))
        if j is not None:
            print(f"  Video {pfeil} {c.UDP_MBIT} Mbit/s  Jitter {farbe(f(r[f'udp_{richtung}_jitter_ms'], ' ms'), c.stufe_udp_jitter(j))}  "
                  f"Verlust {farbe(f(r.get(f'udp_{richtung}_verlust_pct'), ' %'), c.stufe_udp_loss(v))}")
    if r.get("iperf_fehler"):
        print(farbe(f"  ⚠ Durchsatztest fehlgeschlagen: {r['iperf_fehler']}", "schlecht"))
    for label, text, stufe in anzeige.messung_zeilen(r):
        print(f"  {label:<11} {farbe(text, stufe)}")
    if r.get("netze"):
        print(f"  Umgebung    {len(r['netze'])} sichtbare Netze gespeichert (netze.csv)")
    if aps:
        print("  Sichtbare APs:")
        for a in aps:
            s = c.num(a.get("signal_dbm"))
            print(f"   {'●' if a['verbunden'] else ' '} {a['ap']:<24} {c.kanal_name(c.num(a.get('freq_mhz'))):<14} "
                  f"{farbe(f(s, ' dBm'), c.bewertung_signal(s)[1])}{'' if a['quelle'] == 'iw' else ' ≈'}")
        w = c.klebe_warnung(aps)
        if w:
            print(farbe(f"  ⚠ {w}", "mittel"))
    print()


def cmd_report(args):
    sz = None if args.alle_szenarien else (args.szenario or c.aktives_szenario())
    rows = c.report_daten(sz, args.alle)
    if not rows:
        sys.exit("Noch keine Messungen" + (f" für '{sz}'." if sz else "."))
    if sz:
        b = c.lade_szenarien().get(sz)
        print(f"Szenario: {sz}" + (f" — {b}" if b else "") + "\n")
    mit_sz = sz is None
    kopf = (f"{'Szenario':<18} " if mit_sz else "") + f"{'Ort':<18} {'Zeit':<12} {'AP':<18} {'Band':<4} " \
           f"{'dBm':>6} {'min':>5} {'TX':>6} {'RX':>6} {'Loss%':>6} {'RTT':>6} {'Jit':>5} {'Retr%':>6} " \
           f"{'RetL%':>6} {'↓iperf':>7} {'↑iperf':>7} {'Vid↑ms/%':>9} Video  Hinweis"
    print(kopf)
    print("─" * len(kopf))

    def col(v, w=6):
        return f"{(v or '–'):>{w}}"
    for r in rows:
        print((f"{(r.get('szenario') or '–')[:18]:<18} " if mit_sz else "")
              + f"{r['ort'][:18]:<18} {r['zeit'][5:16].replace('T', ' '):<12} {r['ap'][:18]:<18} {r['band']:<4} "
              f"{farbe(col(r['signal_avg']), c.bewertung_signal(c.num(r['signal_avg']))[1])} {col(r['signal_min'], 5)} "
              f"{col(r['tx_bitrate_avg'])} {col(r['rx_bitrate_avg'])} "
              f"{farbe(col(r.get('ping_verlust_pct')), c.stufe_loss(c.num(r.get('ping_verlust_pct'))))} "
              f"{col(r.get('rtt_avg'))} {col(r.get('jitter_ms'), 5)} {col(r.get('retry_quote_pct'))} "
              f"{col(r.get('last_retry_pct'))} "
              f"{col(r.get('iperf_down_mbit'), 7)} {col(r.get('iperf_up_mbit'), 7)} "
              f"{col(r.get('udp_up_jitter_ms') and str(r['udp_up_jitter_ms']) + '/' + str(r.get('udp_up_verlust_pct', '')), 9)}"
              f" {farbe(empfehlung.SYMBOL[r['video']['stufe']] + ' ' + (r['video']['stufe'] or '?'), c.stufe_ampel(r['video']['stufe'])):<14}"
              f" {r['hinweis']}")
    print(f"\nDaten: {c.DATA}")


def cmd_vergleich(args):
    d = c.vergleich_daten(args.wert, args.szenarien or None)
    if not d["zeilen"]:
        sys.exit(f"Keine Werte für '{d['titel']}'" + (" – mit --iperf messen." if args.wert in ("down", "up") else "."))
    szen = d["szenarien"]
    w = max(10, *(min(len(s), 20) for s in szen))
    print(f"{d['titel']} ({d['einheit']}) – {'höher' if d['hoch_gut'] else 'niedriger'} ist besser; "
          f"Unterschiede < {d['toleranz']:g} {d['einheit']} gelten als gleich; (n) = Anzahl Messungen (Median, "
          f"±halbe Spanne); gelb = Spannen überlappen, Unterschied nicht belastbar\n")
    print(f"{'Ort':<18}" + "".join(f" {s[:20]:>{w}}" for s in szen))
    trenn = "─" * (18 + (w + 1) * len(szen))
    print(trenn)

    def zeile(z):
        zellen = []
        for v, n, m, sp in zip(z["werte"], z["n"], z["mark"], z["spanne"]):
            t = "–" if v is None else f"{v:g}" + (f" ±{(sp[1] - sp[0]) / 2:.2g}" if sp else "") + (f" ({n})" if n > 1 else "")
            zellen.append(" " + farbe(f"{t:>{w}}", "mittel" if m == "unsicher" else m))
        print(f"{z['ort'][:18]:<18}" + "".join(zellen))
    for z in d["zeilen"]:
        zeile(z)
    print(trenn)
    for z in d["zusammenfassung"]:
        zeile(z)
    print(f"{'Orte gewonnen':<18}" + "".join(f" {d['siege'][s]:>{w}}" for s in szen))
    if d["fehlend"]:
        print("\nNoch nicht gemessen:")
        for s, o in d["fehlend"].items():
            print(f"  {s}: {', '.join(o)}")
    besch = [(s, d["beschreibungen"].get(s)) for s in szen if d["beschreibungen"].get(s)]
    if besch:
        print()
        for s, b in besch:
            print(f"  {s}: {b}")


def cmd_empfehlung(args):
    d = empfehlung.empfehlung_daten()
    if not d["szenarien"]:
        sys.exit("Noch keine Messungen.")
    szen = d["szenarien"]
    w = max(10, *(min(len(s), 22) for s in szen))
    print("Videocall-Eignung: ● gut  ◐ geht  ○ schlecht  – nicht gemessen  "
          "(~ Messungen widersprechen sich, D = mit Dauertest; es zählt das schlechteste Ergebnis)\n")
    print(f"{'Ort':<26}" + "".join(f" {s[:22]:>{w}}" for s in szen))
    print("─" * (26 + (w + 1) * len(szen)))
    for o in d["orte"]:
        zellen = []
        for s in szen:
            z = d["zellen"].get((o, s))
            if not z:
                zellen.append(" " + f"{'–':>{w}}")
                continue
            t = (empfehlung.SYMBOL[z["stufe"]] + " " + z["stufe"] + ("~" if z["schwankt"] else "")
                 + (" D" if z["dauertest"] else ""))
            zellen.append(" " + farbe(f"{t:>{w}}", c.stufe_ampel(z["stufe"])))
        print(f"{o[:26]:<26}" + "".join(zellen))
    print()
    for i, s in enumerate(d["rangfolge"], 1):
        st = d["pro_sz"][s]
        print(f"  {i}. {s}: {st['gut']} gut · {st['geht']} geht · {st['schlecht']} schlecht"
              f" · {len(st['fehlend'])} fehlen")
    print()
    for satz in d["fazit"]:
        print("  " + satz)
    if args.details:
        print("\nGründe (schlechteste Messung je Zelle):")
        for (o, s), z in d["zellen"].items():
            if z["gruende"]:
                print(f"  {o} · {s}: {'; '.join(z['gruende'])}")


def cmd_kanaele(args):
    emp = umfeld.kanalempfehlung(args.tage)
    if not any(emp.values()) or not c.NETZE.exists():
        sys.exit("Noch keine Netze gespeichert – Messungen mit AP-Scan machen.")
    print(f"Belegung je Kanal aus allen gespeicherten Scans (stärkster Wert je Netz, ab {umfeld.SCHWELLE_DBM} dBm).")
    print("Last = Summe der fremden Signale – je niedriger, desto ruhiger. Ruhigster Kanal zuerst.\n")
    for b, zeilen in emp.items():
        print(f"{b} GHz")
        for i, z in enumerate(zeilen):
            st = f"{z['staerkstes'][0]} {z['staerkstes'][1]:g} dBm" if z["staerkstes"] else "–"
            last = f"{z['last_db']:g} dBm" if z["last_db"] is not None else "frei"
            print(f"  {'→' if i == 0 else ' '} Kanal {z['kanal']:>3} ({z['breite']} MHz){' DFS' if z['dfs'] else '    '}"
                  f"  fremd: {z['fremde']:>2}  Last: {last:>10}  stärkstes: {st}"
                  + (f"  · eigene: {', '.join(z['eigene'])}" if z["eigene"] else ""))
        print()
    print("DFS-Kanäle (52–144) müssen bei Radar kurz abschalten und wechseln – für Calls lieber ein")
    print("ruhiger Nicht-DFS-Kanal, wenn der Unterschied klein ist.")
    wechsel = umfeld.kanalwechsel()
    if wechsel:
        print("\nKanalwechsel deiner APs zwischen Messungen:")
        for w in wechsel:
            print(f"  {w['bei'][:16].replace('T', ' ')}  {w['ap']} {w['band']} GHz: Kanal {w['von']} → {w['nach']}")


def cmd_dauertest(args):
    cfg = c.lade_config()
    szenario = args.szenario or c.aktives_szenario() or szenario_auswaehlen()
    if not szenario:
        sys.exit("Kein Szenario gewählt – erst 'wifi-survey szenario' ausführen.")
    ort = args.ort or ort_auswaehlen(szenario)
    if not ort:
        sys.exit("Abgebrochen.")
    minuten = args.minuten or float(cfg["dauertest_min"])
    countdown = args.countdown if args.countdown is not None else int(cfg["countdown"])
    if countdown and ansage.hinweis():
        print(farbe(f"   ⚠ {ansage.hinweis()}", "mittel"))
    print(f"\n⏱ Dauertest {minuten:g} min · {szenario} · {ort}  (Strg+C beendet vorzeitig und wertet aus)\n")
    stop, erg = threading.Event(), {}

    def sekunde(sek, z):
        print(f"\r   {sek // 60:3}:{sek % 60:02d}  {f(z.get('signal'), ' dBm'):>9}  "
              f"Aussetzer {z['aussetzer']}  gestört {z['gestoerte_s']} s  {z.get('ap') or ''}{' ' * 10}",
              end="", flush=True)

    def status(t):
        print(f"\r   {t}   " if t.startswith("Start in") else f"\n   {t}", end="", flush=True)

    def lauf():
        try:
            erg["r"] = dauertest.dauertest(
                szenario, ort, int(minuten * 60), notiz=args.notiz or "", iface=args.interface,
                ping_ziel=args.ping or cfg["ping_ziel"] or None, iperf_host=args.iperf or cfg["iperf_host"] or None,
                countdown=countdown, on_sekunde=sekunde, on_status=status, stop=stop)
        except Exception as e:  # noqa: BLE001
            erg["fehler"] = e
    t = threading.Thread(target=lauf)
    t.start()
    try:
        while t.is_alive():
            t.join(0.2)
    except KeyboardInterrupt:
        stop.set()
        print("\n   Beende vorzeitig …")
        t.join()
    if isinstance(erg.get("fehler"), c.Abgebrochen):
        sys.exit("\nAbgebrochen (unter 60 s) – nichts gespeichert.")
    if erg.get("fehler"):
        sys.exit(farbe(f"\n✗ {erg['fehler']}", "schlecht"))
    r, sekunden = erg["r"]
    dauertest.speichern(r, sekunden)
    print(f"\n\n── Dauertest: {r['ort']}  [{r['szenario']}] ─────────────────────────────")
    for label, text, stufe in anzeige.dauertest_zeilen(r):
        print(f"  {label:<11} {farbe(text, stufe)}")
    print()


def cmd_dauertests(args):
    tests = dauertest.laden(args.szenario)
    if not tests:
        sys.exit("Noch keine Dauertests.")
    print(f"{'Zeit':<12} {'Szenario':<22} {'Ort':<24} {'Dauer':>6} {'Ausse.':>6} {'längst':>6} "
          f"{'↑Verl%':>6} {'↓Verl%':>6} {'RTT99':>6}  Urteil")
    for r in tests:
        u = dauertest.urteil(r)
        dauer = int(c.num(r["dauer_s"]) or 0)
        print(f"{r['zeit'][5:16].replace('T', ' '):<12} {r['szenario'][:22]:<22} {r['ort'][:24]:<24} "
              f"{dauer // 60:>3}:{dauer % 60:02d} {r['aussetzer']:>6} {r['laengster_aussetzer_s']:>5}s "
              f"{f(r.get('udp_up_verlust_pct')):>6} {f(r.get('udp_down_verlust_pct')):>6} {f(r.get('rtt_p99')):>6}  "
              + farbe(empfehlung.SYMBOL[u['stufe']] + " " + u["stufe"] + (" – " + "; ".join(u["gruende"]) if u["gruende"] else ""),
                      c.stufe_ampel(u["stufe"])))


def cmd_szenario(args):
    if args.name:
        name = c.szenario_speichern(args.name, args.beschreibung or "")
        c.szenario_aktivieren(name)
    else:
        name = szenario_auswaehlen()
    if name:
        b = c.lade_szenarien().get(name)
        print(f"Aktives Szenario: {name}" + (f" — {b}" if b else ""))


def cmd_iperftest(args):
    cfg = c.lade_config()
    host = args.host or cfg["iperf_host"]
    if not host:
        sys.exit("Kein Server angegeben: wifi-survey iperftest <IP>")
    print(f"Teste iperf3-Server {host} (3 s je Richtung) …")
    down, up, fehler = c.iperf_schnelltest(host)
    if fehler:
        sys.exit(farbe(f"✗ {fehler}", "schlecht"))
    print(farbe(f"✓ ↓ {f(down)} Mbit/s   ↑ {f(up)} Mbit/s", "gut"))
    if args.host:
        cfg["iperf_host"] = host
        c.speichere_config(cfg)
        print("Server als Standard gespeichert – gilt für TUI und 'wifi-survey messen'.")


def bearbeiten(pfad, init):
    if not pfad.exists():
        init()
    editor = os.environ.get("EDITOR", "nvim")
    os.execvp(editor, [editor, str(pfad)])


# --------------------------------------------------------------------------- main

def main(argv=None):
    p = argparse.ArgumentParser(prog="wifi-survey", description="WLAN-Vermessung mit Messpunkten und Szenarien. "
                                "Ohne Unterbefehl startet die TUI.")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd")
    m = sub.add_parser("messen", help="Messung auf der Kommandozeile")
    m.add_argument("-d", "--dauer", type=int, help="Messdauer in Sekunden")
    m.add_argument("-o", "--ort", help="Ort direkt angeben statt auswählen")
    m.add_argument("-n", "--notiz", help="Notiz zur Messung")
    m.add_argument("-s", "--szenario", help="Szenario (Standard: aktives)")
    m.add_argument("--iperf", metavar="HOST", help="iperf3-Server für Durchsatztest")
    m.add_argument("--ping", metavar="HOST", help="Ping-Ziel (Standard: Gateway)")
    m.add_argument("-i", "--interface", help="WLAN-Interface")
    m.add_argument("--kein-scan", action="store_true", help="AP-Scan überspringen")
    m.add_argument("-c", "--countdown", type=int, metavar="SEK",
                   help="Vorlauf in Sekunden mit Signaltönen und Sprachansagen (0 = aus, Standard: aus config.json)")
    m.add_argument("--kein-udp", action="store_true",
                   help=f"Video-Test (UDP {c.UDP_MBIT} Mbit/s je Richtung über iperf3) überspringen")
    r = sub.add_parser("report", help="Übersicht der Messungen")
    r.add_argument("--alle", action="store_true", help="alle Messungen statt nur der letzten je Ort")
    r.add_argument("-s", "--szenario", help="Szenario anzeigen (Standard: aktives)")
    r.add_argument("-S", "--alle-szenarien", action="store_true", help="alle Szenarien anzeigen")
    s = sub.add_parser("szenario", help="aktives Szenario wählen/anlegen")
    s.add_argument("name", nargs="?", help="Name (wird angelegt, falls neu)")
    s.add_argument("-b", "--beschreibung", help="Beschreibung des Setups")
    v = sub.add_parser("vergleich", help="Orte × Szenarien vergleichen")
    v.add_argument("-w", "--wert", choices=list(c.METRIKEN), default="signal", help="Kennzahl (Standard: signal)")
    v.add_argument("szenarien", nargs="*", help="nur diese Szenarien (Standard: alle)")
    e = sub.add_parser("empfehlung", help="Videocall-Eignung: Orte × Szenarien, bestes Setup, Lücken")
    e.add_argument("--details", action="store_true", help="Gründe je Zelle anzeigen")
    k = sub.add_parser("kanaele", help="Kanalbelegung, ruhigste Kanäle und Kanalwechsel deiner APs")
    k.add_argument("--tage", type=int, help="nur Scans der letzten N Tage")
    dt = sub.add_parser("dauertest", help="minutenlanger Test an einem Platz (Aussetzer finden)")
    dt.add_argument("-m", "--minuten", type=float, help="Dauer in Minuten (Standard: config.json, sonst 15)")
    dt.add_argument("-o", "--ort", help="Ort direkt angeben statt auswählen")
    dt.add_argument("-n", "--notiz", help="Notiz")
    dt.add_argument("-s", "--szenario", help="Szenario (Standard: aktives)")
    dt.add_argument("--iperf", metavar="HOST", help="iperf3-Server (ohne: nur Ping)")
    dt.add_argument("--ping", metavar="HOST", help="Ping-Ziel (Standard: Gateway)")
    dt.add_argument("-i", "--interface", help="WLAN-Interface")
    dt.add_argument("-c", "--countdown", type=int, metavar="SEK", help="Vorlauf mit Ansagen (0 = aus)")
    dl = sub.add_parser("dauertests", help="bisherige Dauertests auflisten")
    dl.add_argument("-s", "--szenario", help="nur dieses Szenario")
    sub.add_parser("szenarien", help="Szenarien im Editor bearbeiten")
    sub.add_parser("orte", help="Ortsliste im Editor bearbeiten")
    sub.add_parser("aps", help="AP-Namen im Editor bearbeiten")
    sub.add_parser("pfad", help="Datenordner ausgeben")
    it = sub.add_parser("iperftest", help="iperf3-Server testen und als Standard speichern")
    it.add_argument("host", nargs="?", help="IP oder IP:Port (Standard: gespeicherter Server)")
    args = p.parse_args(argv)

    if args.cmd is None:
        from .tui import run
        run()
    elif args.cmd == "messen":
        cmd_messen(args)
    elif args.cmd == "report":
        cmd_report(args)
    elif args.cmd == "vergleich":
        cmd_vergleich(args)
    elif args.cmd == "szenario":
        cmd_szenario(args)
    elif args.cmd == "empfehlung":
        cmd_empfehlung(args)
    elif args.cmd == "kanaele":
        cmd_kanaele(args)
    elif args.cmd == "dauertest":
        cmd_dauertest(args)
    elif args.cmd == "dauertests":
        cmd_dauertests(args)
    elif args.cmd == "szenarien":
        bearbeiten(c.SZENARIEN, lambda: c.SZENARIEN.write_text("# Name = Beschreibung des Setups\n"))
    elif args.cmd == "orte":
        bearbeiten(c.ORTE, c.lade_orte)
    elif args.cmd == "aps":
        bearbeiten(c.APS, lambda: c.APS.write_text("# BSSID-Präfix = Name\n"))
    elif args.cmd == "iperftest":
        cmd_iperftest(args)
    elif args.cmd == "pfad":
        print(c.DATA)


if __name__ == "__main__":
    main()
