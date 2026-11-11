"""Funkumfeld: Kanalbelegung, Überlappungen, Kanalwechsel und Kanalempfehlung aus netze.csv/scans.csv.

nmcli liefert nur den Hauptkanal und die Breite. Den belegten Frequenzbereich rechnen wir
daraus nach den festen Kanalblöcken aus (5 GHz) bzw. nehmen bei 40 MHz auf 2,4 GHz die
übliche Lage an (Kanal 1–7: Erweiterung nach oben, sonst nach unten).
"""

import math

from . import core as c

# 5-GHz-Blöcke (erster Kanal je Block), wie sie Router für 40/80/160 MHz bündeln
BLOECKE_5G = {
    40: [36, 44, 52, 60, 100, 108, 116, 124, 132, 140, 149, 157],
    80: [36, 52, 100, 116, 132, 149],
    160: [36, 100],
}
DFS = range(52, 145)  # Kanäle mit Radarerkennung (Kanalwechsel möglich)
SCHWELLE_DBM = -82    # schwächere Netze hält ein WLAN-Gerät nicht für "belegt" (CCA-Schwelle)
KANDIDATEN = {        # sinnvolle Wahlmöglichkeiten je Band: (Kanal, Breite)
    "2.4": [(1, 20), (6, 20), (11, 20)],
    "5": [(36, 80), (52, 80), (100, 80), (116, 80), (132, 80)],
}


def kanal_freq(kanal, band):
    if band == "2.4":
        return 2484 if kanal == 14 else 2407 + 5 * kanal
    if band == "5":
        return 5000 + 5 * kanal
    return 5950 + 5 * kanal


def bereich(kanal, freq, breite, mitte=None):
    """Belegter Frequenzbereich (von, bis) in MHz."""
    breite = int(breite or 20)
    if mitte:
        return mitte - breite / 2, mitte + breite / 2
    if freq is None and kanal is None:
        return None
    b = c.band(freq) if freq else ("2.4" if kanal <= 14 else "5")
    kanal = int(kanal) if kanal is not None else _kanal_aus_freq(freq)
    f = freq or kanal_freq(kanal, b)
    if breite <= 20:
        return f - 10, f + 10
    if b == "2.4":
        return (f - 10, f + 30) if kanal <= 7 else (f - 30, f + 10)
    if b == "5":
        for start in reversed(BLOECKE_5G.get(breite, [])):
            if kanal >= start:
                lo = kanal_freq(start, "5") - 10
                return lo, lo + breite
    return f - breite / 2, f + breite / 2


def _kanal_aus_freq(freq):
    f = int(freq)
    if f < 3000:
        return 14 if f == 2484 else (f - 2407) // 5
    return (f - 5000) // 5 if f < 5925 else (f - 5950) // 5


def ueberlappt(a, b):
    return a and b and a[0] < b[1] and b[0] < a[1]


def _eigene_ssids():
    """SSIDs, mit denen je gemessen wurde – das ist "dein" Netz (inkl. Test-WLANs)."""
    return {r.get("ssid") for r in c.read_csv(c.MESSUNGEN) if r.get("ssid")}


def _geraet(bssid):
    """Gerätekennung: die letzten vier Oktette (Gastnetze haben oft nur ein anderes erstes Oktett)."""
    return ":".join((bssid or "").lower().split(":")[-4:])


def _eigene_geraete(netze, ssids):
    return {_geraet(n["bssid"]) for n in netze if n.get("ssid") in ssids}


# --------------------------------------------------------------------------- je Messung

def lage_der_messung(r, netze=None):
    """Überlappende Netze für den Kanal, auf dem die Messung lief.

    -> {"bereich": (von, bis), "fremde": [...], "eigene": [...]} oder None (kein Scan gespeichert)
    Jeder Eintrag: dict aus netze.csv, sortiert nach Signal. Nur Netze ab SCHWELLE_DBM zählen.
    """
    netze = netze if netze is not None else [n for n in c.read_csv(c.NETZE) if n.get("id") == r.get("id")]
    if not netze:
        return None
    eigen_b = bereich(None, c.num(r.get("freq_mhz")), c.num(r.get("kanalbreite_mhz"))
                      or next((c.num(n.get("bandbreite_mhz")) for n in netze if n.get("verbunden")), 20),
                      c.num(r.get("kanalmitte_mhz")))
    ssids = _eigene_ssids() | {r.get("ssid")}
    eigene_ger = _eigene_geraete(netze, ssids)
    fremde, eigene = [], []
    for n in netze:
        if n.get("verbunden") or (n.get("bssid") or "").lower() == (r.get("bssid") or "").lower():
            continue
        s = c.num(n.get("signal_dbm"))
        if s is None or s < SCHWELLE_DBM:
            continue
        b = bereich(c.num(n.get("kanal")), c.num(n.get("freq_mhz")), c.num(n.get("bandbreite_mhz")))
        if not ueberlappt(eigen_b, b):
            continue
        (eigene if n.get("ssid") in ssids or _geraet(n["bssid"]) in eigene_ger else fremde).append(n)
    key = lambda n: -(c.num(n.get("signal_dbm")) or -999)  # noqa: E731
    return {"bereich": eigen_b, "fremde": sorted(fremde, key=key), "eigene": sorted(eigene, key=key)}


def lage_text(lage):
    if not lage:
        return None
    teile = []
    if lage["fremde"]:
        st = lage["fremde"][0]
        teile.append(f"{len(lage['fremde'])} fremde Netze überlappen "
                     f"(stärkstes: {st.get('ssid') or '(verborgen)'} {c.num(st.get('signal_dbm')):g} dBm)")
    else:
        teile.append("keine fremden Netze auf dem Kanal")
    if lage["eigene"]:
        teile.append(f"{len(lage['eigene'])} eigene auf demselben Kanal")
    return ", ".join(teile)


# --------------------------------------------------------------------------- Kanalwechsel

def kanal_historie():
    """Kanal je eigenem AP über die Zeit, aus scans.csv (eigenes Netz) und netze.csv.

    -> {bssid: [(zeit, id, freq_mhz), …]} zeitlich sortiert
    """
    zeiten = {r["id"]: r["zeit"] for r in c.read_csv(c.MESSUNGEN)}
    ssids = _eigene_ssids()
    hist = {}
    for quelle in (c.read_csv(c.SCANS), c.read_csv(c.NETZE)):
        for n in quelle:
            if n.get("id") not in zeiten or (n.get("ssid") and n.get("ssid") not in ssids):
                continue
            f = c.num(n.get("freq_mhz"))
            if f:
                hist.setdefault(n["bssid"].lower(), {})[n["id"]] = (zeiten[n["id"]], n["id"], f)
    return {b: sorted(v.values()) for b, v in hist.items()}


def kanalwechsel(mid=None, hist=None):
    """Kanalwechsel eigener APs. Mit mid nur die, die vor dieser Messung zuletzt anders waren.

    -> [{"ap", "bssid", "band", "von", "nach", "seit", "bei"}]
    """
    hist = hist if hist is not None else kanal_historie()
    namen = c.lade_aps()
    out = []
    for bssid, eintraege in hist.items():
        for (z0, i0, f0), (z1, i1, f1) in zip(eintraege, eintraege[1:]):
            if f0 != f1 and (mid is None or i1 == mid):
                out.append({"ap": c.ap_name(bssid, namen), "bssid": bssid, "band": c.band(f1),
                            "von": _kanal_aus_freq(f0), "nach": _kanal_aus_freq(f1), "seit": z0, "bei": z1})
    return sorted(out, key=lambda w: w["bei"])


# --------------------------------------------------------------------------- Empfehlung

def kanalempfehlung(tage=None):
    """Belastung je Kandidatenkanal aus allen gespeicherten Netzen (stärkster Wert je BSSID).

    -> {band: [{"kanal", "breite", "dfs", "fremde", "eigene", "staerkstes", "last_db"}, …]} bestes zuerst
    last_db: Summe der Leistung aller überlappenden fremden Netze in dBm (je niedriger, desto ruhiger).
    """
    netze = c.read_csv(c.NETZE)
    if tage:
        from datetime import datetime, timedelta
        zeiten = {r["id"]: r["zeit"] for r in c.read_csv(c.MESSUNGEN)}
        grenze = (datetime.now() - timedelta(days=tage)).isoformat()
        netze = [n for n in netze if zeiten.get(n["id"], "") >= grenze]
    staerkste = {}
    for n in netze:
        s = c.num(n.get("signal_dbm"))
        if s is not None and (n["bssid"] not in staerkste or s > c.num(staerkste[n["bssid"]]["signal_dbm"])):
            staerkste[n["bssid"]] = n
    ssids = _eigene_ssids()
    eigene_ger = _eigene_geraete(list(staerkste.values()), ssids)
    erg = {}
    for b, kand in KANDIDATEN.items():
        zeilen = []
        for kanal, breite in kand:
            kb = bereich(kanal, None, breite)
            fremde, eigene, mw = [], [], 0.0
            for n in staerkste.values():
                s = c.num(n["signal_dbm"])
                if c.band(c.num(n.get("freq_mhz"))) != b or s < SCHWELLE_DBM:
                    continue
                if not ueberlappt(kb, bereich(c.num(n.get("kanal")), c.num(n.get("freq_mhz")),
                                              c.num(n.get("bandbreite_mhz")))):
                    continue
                if n.get("ssid") in ssids or _geraet(n["bssid"]) in eigene_ger:
                    eigene.append(n)
                else:
                    fremde.append(n)
                    mw += 10 ** (s / 10)
            st = max(fremde, key=lambda n: c.num(n["signal_dbm"]), default=None)
            zeilen.append({"kanal": kanal, "breite": breite, "dfs": b == "5" and kanal in DFS,
                           "fremde": len(fremde), "eigene": sorted({n.get("ssid") or "?" for n in eigene}),
                           "staerkstes": (st.get("ssid") or "(verborgen)", c.num(st["signal_dbm"])) if st else None,
                           "last_db": round(10 * math.log10(mw), 1) if mw else None})
        # ruhigster zuerst; bei Gleichstand ohne DFS und ohne eigene Netze bevorzugen
        zeilen.sort(key=lambda z: (z["last_db"] if z["last_db"] is not None else -999, z["dfs"], len(z["eigene"])))
        erg[b] = zeilen
    return erg
