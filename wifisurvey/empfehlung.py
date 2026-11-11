"""Gesamtbild für Videokonferenzen: Orte × Szenarien mit Ampel, bestes Setup je Ort, Lücken."""

from itertools import combinations

from . import core as c
from . import dauertest

SYMBOL = {"gut": "●", "geht": "◐", "schlecht": "○", "": "?"}
KOMBI_HINWEIS = ("Gerechnet aus Einzelmessungen. Es gilt nur, wenn beide Setups gleichzeitig laufen können "
                 "(zwei Positionen desselben Geräts brauchen ein zweites Gerät), auf verschiedenen Kanälen funken "
                 "und der Laptop am jeweiligen Ort den besseren AP wählt – vor der Entscheidung zusammen aufbauen "
                 "und nachmessen.")


def empfehlung_daten():
    """-> dict mit
    orte, szenarien,
    zellen[(ort, sz)] = {"stufe", "n", "schwankt", "gruende", "dauertest", "reserve", "knapp", "aps"}
                        (fehlt = nicht gemessen; reserve = kleinste Signalreserve in dB, knapp = gut mit wenig Reserve),
    pro_ort[ort] = {"stufe", "beste": [sz, …]},
    pro_sz[sz] = {"gut", "geht", "schlecht", "gemessen", "fehlend": [ort], "schwach": [(ort, stufe)]},
    rangfolge: [sz, …] bestes zuerst, kombinationen: siehe kombinationen(), fazit: [Satz, …]
    """
    rows = c.read_csv(c.MESSUNGEN)
    tests = dauertest.laden()
    alle_sz = list(c.lade_szenarien())
    vorhanden = {r.get("szenario") for r in rows + tests if r.get("szenario")}
    szen = [s for s in alle_sz if s in vorhanden] + sorted(vorhanden - set(alle_sz))
    orte = c.lade_orte()
    orte += sorted({r["ort"] for r in rows + tests} - set(orte))

    namen = c.lade_aps()
    urteile, aps = {}, {}
    for r in rows:
        u = c.videocall_urteil(r)
        if u["stufe"]:
            urteile.setdefault((r["ort"], r.get("szenario")), []).append(("m", u))
            aps.setdefault((r["ort"], r.get("szenario")), set()).add(c.ap_name(r.get("bssid"), namen))
    for t in tests:
        u = dauertest.urteil(t)
        urteile.setdefault((t["ort"], t.get("szenario")), []).append(("d", u))
        aps.setdefault((t["ort"], t.get("szenario")), set()).add(c.ap_name(t.get("bssid"), namen))

    zellen = {}
    for k, us in urteile.items():
        # konservativ: das schlechteste Urteil zählt – ein Call soll auch im ungünstigen Moment laufen
        schlechtestes = min(us, key=lambda x: x[1]["punkte"])[1]
        d = [u for art, u in us if art == "d"]
        reserve = min((u["reserve_db"] for _, u in us if u.get("reserve_db") is not None), default=None)
        zellen[k] = {"stufe": schlechtestes["stufe"], "punkte": schlechtestes["punkte"], "n": len(us),
                     "schwankt": len({u["punkte"] for _, u in us}) > 1, "gruende": schlechtestes["gruende"],
                     "dauertest": min(d, key=lambda u: u["punkte"])["stufe"] if d else "",
                     "reserve": reserve, "aps": sorted(a for a in aps.get(k, ()) if a),
                     "knapp": schlechtestes["punkte"] == 2 and reserve is not None and reserve < c.RESERVE_KNAPP_DB}

    pro_ort = {}
    for o in orte:
        gemessen = [(s, zellen[(o, s)]) for s in szen if (o, s) in zellen]
        if not gemessen:
            pro_ort[o] = {"stufe": "", "beste": []}
            continue
        p = max(z["punkte"] for _, z in gemessen)
        pro_ort[o] = {"stufe": c.STUFEN[p], "beste": [s for s, z in gemessen if z["punkte"] == p]}

    pro_sz = {}
    for s in szen:
        st = {"gut": 0, "geht": 0, "schlecht": 0, "gemessen": 0, "fehlend": [], "schwach": []}
        for o in orte:
            z = zellen.get((o, s))
            if not z:
                st["fehlend"].append(o)
                continue
            st["gemessen"] += 1
            st[z["stufe"]] += 1
            if z["stufe"] != "gut":
                st["schwach"].append((o, z["stufe"]))
        pro_sz[s] = st

    meiste = max((pro_sz[s]["gemessen"] for s in szen), default=0)

    def rang(s):
        st = pro_sz[s]
        g = st["gemessen"] or 1
        # Setups mit weniger als halb so vielen gemessenen Orten wie das vollständigste kommen nach hinten
        return (st["gemessen"] < meiste / 2, -st["gut"] / g, -(st["gut"] + st["geht"]) / g, -st["gemessen"])
    rangfolge = sorted(szen, key=rang)
    kombis = kombinationen(orte, szen, zellen, pro_sz)
    return {"orte": orte, "szenarien": szen, "zellen": zellen, "pro_ort": pro_ort, "pro_sz": pro_sz,
            "rangfolge": rangfolge, "kombinationen": kombis,
            "fazit": _fazit(orte, rangfolge, pro_sz, pro_ort, zellen, kombis),
            "beschreibungen": c.lade_szenarien()}


def kombinationen(orte, szen, zellen, pro_sz):
    """Paare von Setups, die zusammen mehr Orte gut abdecken als jedes der beiden allein.

    Je Ort zählt das bessere der beiden (bei gleichem Urteil das mit mehr Signalreserve).
    -> [{"setups": (a, b), "gut", "geht", "schlecht", "fehlend": [ort], "schwach": [(ort, stufe)],
         "versorgt": {setup: {ap-Namen: [ort, …]}}}], beste zuerst
    """
    out = []
    for a, b in combinations(szen, 2):
        k = {"setups": (a, b), "gut": 0, "geht": 0, "schlecht": 0, "fehlend": [], "schwach": [],
             "versorgt": {a: {}, b: {}}}
        for o in orte:
            wahl = [(zellen[(o, s)], s) for s in (a, b) if (o, s) in zellen]
            if not wahl:
                k["fehlend"].append(o)
                continue
            z, s = max(wahl, key=lambda x: (x[0]["punkte"], -99 if x[0]["reserve"] is None else x[0]["reserve"]))
            k[z["stufe"]] += 1
            if z["stufe"] != "gut":
                k["schwach"].append((o, z["stufe"]))
            k["versorgt"][s].setdefault(" / ".join(z["aps"]), []).append(o)
        if k["gut"] > max(pro_sz[a]["gut"], pro_sz[b]["gut"]):
            out.append(k)
    return sorted(out, key=lambda k: (-k["gut"], k["schlecht"], len(k["fehlend"])))


def kombination_zeilen(k):
    """Textzeilen zu einer Kombination: wer versorgt was, was bleibt schwach."""
    z = []
    for s in k["setups"]:
        gruppen = k["versorgt"][s]
        if not gruppen:
            z.append(f"{s}: trägt nichts bei")
        for ap, orte in gruppen.items():
            ueber = f" über {ap}" if ap and len(gruppen) > 1 else ""
            z.append(f"{s}{ueber}: {', '.join(orte)}")
    if k["schwach"]:
        z.append("bleibt schwach: " + ", ".join(f"{o} ({stufe})" for o, stufe in k["schwach"]))
    if k["fehlend"]:
        z.append("in beiden nicht gemessen: " + ", ".join(k["fehlend"]))
    return z


def _fazit(orte, rangfolge, pro_sz, pro_ort, zellen=None, kombis=None):
    if not rangfolge:
        return ["Noch keine Messungen."]
    s = []
    best = rangfolge[0]
    st = pro_sz[best]
    satz = f"Bestes Setup: „{best}“ – gut an {st['gut']} von {st['gemessen']} gemessenen Orten"
    if st["fehlend"]:
        satz += f" ({len(st['fehlend'])} Orte noch nicht gemessen)"
    s.append(satz + ".")
    if st["schwach"]:
        s.append("Schwach dort: " + ", ".join(f"{o} ({stufe})" for o, stufe in st["schwach"]) + ".")
    luecken = [o for o in orte if pro_ort[o]["stufe"] and pro_ort[o]["stufe"] != "gut"]
    if luecken:
        s.append("In keinem Setup gut: " + ", ".join(
            f"{o} (bestenfalls {pro_ort[o]['stufe']} mit {' / '.join(pro_ort[o]['beste'])})" for o in luecken) + ".")
    ungemessen = [o for o in orte if not pro_ort[o]["stufe"]]
    if ungemessen:
        s.append("Noch gar nicht gemessen: " + ", ".join(ungemessen) + ".")
    anzahl = {sz: pro_sz[sz]["gemessen"] for sz in rangfolge}
    if len(set(anzahl.values())) > 1:
        s.append("Achtung, unterschiedlich viele Orte gemessen (" + ", ".join(f"{sz}: {n}" for sz, n in anzahl.items())
                 + ") – die Rangfolge ist nur auf gemeinsamen Orten wirklich fair.")
    andere = [o for o in orte if pro_ort[o]["stufe"] == "gut" and best not in pro_ort[o]["beste"]]
    if andere:
        s.append("Besser mit anderem Setup: " + ", ".join(
            f"{o} → {' / '.join(pro_ort[o]['beste'])}" for o in andere) + ".")
    knapp = [(o, sz, z["reserve"]) for (o, sz), z in (zellen or {}).items() if z["knapp"]]
    if knapp:
        s.append(f"Gut, aber knapp (unter {c.RESERVE_KNAPP_DB} dB Signalreserve, kann an einem schlechten Tag kippen): "
                 + ", ".join(f"{o} mit {sz} ({r:g} dB)" for o, sz, r in knapp) + ".")
    if kombis:
        k = kombis[0]
        s.append(f"Beste Kombination: „{k['setups'][0]}“ + „{k['setups'][1]}“ – gut an {k['gut']} von {len(orte)} Orten "
                 f"(„{best}“ allein: {st['gut']}).")
    return s
