"""Gesamtbild für Videokonferenzen: Orte × Szenarien mit Ampel, bestes Setup je Ort, Lücken."""

from . import core as c
from . import dauertest

SYMBOL = {"gut": "●", "geht": "◐", "schlecht": "○", "": "?"}


def empfehlung_daten():
    """-> dict mit
    orte, szenarien,
    zellen[(ort, sz)] = {"stufe", "n", "schwankt", "gruende", "dauertest"} (fehlt = nicht gemessen),
    pro_ort[ort] = {"stufe", "beste": [sz, …]},
    pro_sz[sz] = {"gut", "geht", "schlecht", "gemessen", "fehlend": [ort], "schwach": [(ort, stufe)]},
    rangfolge: [sz, …] bestes zuerst, fazit: [Satz, …]
    """
    rows = c.read_csv(c.MESSUNGEN)
    tests = dauertest.laden()
    alle_sz = list(c.lade_szenarien())
    vorhanden = {r.get("szenario") for r in rows + tests if r.get("szenario")}
    szen = [s for s in alle_sz if s in vorhanden] + sorted(vorhanden - set(alle_sz))
    orte = c.lade_orte()
    orte += sorted({r["ort"] for r in rows + tests} - set(orte))

    urteile = {}
    for r in rows:
        u = c.videocall_urteil(r)
        if u["stufe"]:
            urteile.setdefault((r["ort"], r.get("szenario")), []).append(("m", u))
    for t in tests:
        u = dauertest.urteil(t)
        urteile.setdefault((t["ort"], t.get("szenario")), []).append(("d", u))

    zellen = {}
    for k, us in urteile.items():
        # konservativ: das schlechteste Urteil zählt – ein Call soll auch im ungünstigen Moment laufen
        schlechtestes = min(us, key=lambda x: x[1]["punkte"])[1]
        d = [u for art, u in us if art == "d"]
        zellen[k] = {"stufe": schlechtestes["stufe"], "punkte": schlechtestes["punkte"], "n": len(us),
                     "schwankt": len({u["punkte"] for _, u in us}) > 1, "gruende": schlechtestes["gruende"],
                     "dauertest": min(d, key=lambda u: u["punkte"])["stufe"] if d else ""}

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
    return {"orte": orte, "szenarien": szen, "zellen": zellen, "pro_ort": pro_ort, "pro_sz": pro_sz,
            "rangfolge": rangfolge, "fazit": _fazit(orte, rangfolge, pro_sz, pro_ort),
            "beschreibungen": c.lade_szenarien()}


def _fazit(orte, rangfolge, pro_sz, pro_ort):
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
    return s
