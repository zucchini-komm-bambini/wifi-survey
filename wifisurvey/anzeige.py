"""Gemeinsame Textbausteine für TUI und CLI: (Beschriftung, Text, Farbstufe)."""

from datetime import datetime

from . import core as c
from . import dauertest
from . import empfehlung
from . import umfeld


def _w(v):
    """Wert oder Strich – 0 ist ein Wert."""
    return "–" if v in (None, "") else v


def _datum(iso):
    try:
        return datetime.fromisoformat(iso).strftime("%d.%m. %H:%M")
    except (TypeError, ValueError):
        return iso or "?"


def urteil_zeile(u):
    if not u["stufe"]:
        return "?", ""
    t = f"{empfehlung.SYMBOL[u['stufe']]} {u['stufe']}"
    if u["gruende"]:
        t += " – " + "; ".join(u["gruende"])
    reserve = u.get("reserve_db")
    if reserve is not None and not any("Reserve" in g for g in u["gruende"]):
        t += " · " + c.reserve_text(reserve) + (" (knapp)" if reserve < c.RESERVE_KNAPP_DB else "")
    return t + f"  (Basis: {u['basis']})", c.stufe_ampel(u["stufe"])


def vorab_zeile(r):
    warn = r.get("vorab_warnungen")
    if warn is None:  # aus gespeicherten Feldern rekonstruieren
        warn = []
        if r.get("power_save") == "on":
            warn.append("WLAN-Stromsparmodus an")
        if r.get("netzteil") == "nein":
            warn.append("auf Akku")
        if r.get("vpn"):
            warn.append(f"VPN aktiv ({r['vpn']})")
        if r.get("bluetooth") == "an" and c.band(c.num(r.get("freq_mhz"))) == "2.4":
            warn.append("Bluetooth an (2,4 GHz)")
        if not any(r.get(k) for k in ("power_save", "netzteil", "bluetooth")):
            return None  # alte Messung ohne Vorab-Check
    if warn:
        return "⚠ " + " · ".join(w.split(" – ")[0] for w in warn), "mittel"
    teile = ["Netzteil" if r.get("netzteil") == "ja" else "",
             "Stromsparmodus aus" if r.get("power_save") == "off" else "", "kein VPN"]
    return "✓ " + ", ".join(t for t in teile if t), "gut"


def messung_zeilen(r):
    """Zusätzliche Ergebniszeilen einer Messung (nach den Rohwerten anzuzeigen)."""
    z = [("Videocall",) + urteil_zeile(c.videocall_urteil(r))]
    v = vorab_zeile(r)
    if v:
        z.append(("Vorab-Check",) + v)
    lage = umfeld.lage_der_messung(r, r.get("netze") or None)
    if lage:
        z.append(("Funkumfeld", umfeld.lage_text(lage), "mittel" if lage["fremde"] else ""))
    je_ap = {}
    for w in umfeld.kanalwechsel(r.get("id")):
        je_ap.setdefault(w["ap"], []).append(w)
    for ap, ws in je_ap.items():
        z.append(("Kanalwechsel", f"{ap}: " + ", ".join(f"{w['band']} GHz {w['von']} → {w['nach']}" for w in ws)
                  + f" (zuletzt gemessen {_datum(min(w['seit'] for w in ws))})", "mittel"))
    return z


def dauertest_zeilen(r):
    n = c.num
    z = [("Videocall",) + urteil_zeile(dauertest.urteil(r))]
    dauer = int(n(r.get("dauer_s")) or 0)
    z.append(("Dauer", f"{dauer // 60} min {dauer % 60:02d} s" + (" (vorzeitig beendet)" if r.get("vorzeitig") else ""), ""))
    a = int(n(r.get("aussetzer")) or 0)
    z.append(("Aussetzer", f"{a}" + (f", längster {r['laengster_aussetzer_s']} s, "
                                     f"{r['gestoerte_s']} s gestört insgesamt" if a else ""),
              "gut" if not a else "mittel"))
    if r.get("udp_down_verlust_pct") not in (None, "") or r.get("udp_up_verlust_pct") not in (None, ""):
        z.append(("Video ↑ / ↓", f"Verlust {_w(r.get('udp_up_verlust_pct'))} / {_w(r.get('udp_down_verlust_pct'))} %   "
                                 f"Jitter {_w(r.get('udp_up_jitter_ms'))} / {_w(r.get('udp_down_jitter_ms'))} ms", ""))
    mf = int(n(r.get("messfehler_s")) or 0)
    if mf:
        z.append(("Messfehler", f"{mf} s Videostrom ohne Daten bei intaktem Ping – liegt nicht am WLAN, "
                                "zählt nicht als Aussetzer", "mittel"))
    z.append(("Ping", f"Verlust {_w(r.get('ping_verlust_pct'))} %   RTT Ø {_w(r.get('rtt_avg'))} ms, "
                      f"99 % unter {_w(r.get('rtt_p99'))} ms, max {_w(r.get('rtt_max'))} ms", ""))
    z.append(("Verbindung", f"{_w(r.get('ap'))} ({c.kanal_name(n(r.get('freq_mhz')))}), "
                            f"Signal Ø {_w(r.get('signal_avg'))} / min {_w(r.get('signal_min'))} dBm, "
                            f"{r.get('roams') or 0}× AP-Wechsel, {r.get('kanalwechsel') or 0}× Kanalwechsel", ""))
    v = vorab_zeile(r)
    if v:
        z.append(("Vorab-Check",) + v)
    if r.get("iperf_fehler"):
        z.append(("⚠ iperf3", r["iperf_fehler"], "mittel"))
    return z
