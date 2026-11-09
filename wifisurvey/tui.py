"""Textual-Oberfläche für wifi-survey."""

import threading
import tomllib
from collections import deque
from pathlib import Path

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.theme import Theme
from textual.widgets import (Button, Checkbox, DataTable, Footer, Input, Label, OptionList, ProgressBar,
                             Select, Static, TabbedContent, TabPane)
from textual.widgets.option_list import Option

from . import __version__
from . import core as c

THEME_DATEI = Path.home() / ".local/state/omarchy/current/theme/colors.toml"
ALLE = "__alle__"

# Ampelfarben – werden durch das Omarchy-Theme ersetzt, falls vorhanden
FARBE = {"gut": "green", "mittel": "yellow", "schlecht": "red", "best": "bold green", "worst": "red", "": ""}


def lade_omarchy_theme():
    try:
        t = tomllib.loads(THEME_DATEI.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return None
    g = lambda k, d: t.get(k) or d  # noqa: E731
    FARBE.update(gut=g("green", "green"), mittel=g("yellow", "yellow"), schlecht=g("red", "red"),
                 best="bold " + g("bright_green", g("green", "green")), worst=g("red", "red"))
    return Theme(
        name="omarchy",
        dark=t.get("mode", "dark") != "light",
        primary=g("accent", "#88c0d0"),
        secondary=g("blue", "#81a1c1"),
        accent=g("orange", g("accent", "#ebcb8b")),
        warning=g("yellow", "#ebcb8b"),
        error=g("red", "#bf616a"),
        success=g("green", "#a3be8c"),
        foreground=g("foreground", "#d8dee9"),
        background=g("background", "#2e3440"),
        surface=g("lighter_background", g("background", "#3b4252")),
        panel=g("selection", g("lighter_background", "#434c5e")),
    )


BLOECKE = " ▁▂▃▄▅▆▇█"
SKALA = (-90, -30)  # dBm-Bereich des Signalverlaufs


def signal_chart(werte, breite, hoehe=4):
    """Balkendiagramm mit fester dBm-Skala, Farbe je Bewertung, neueste Werte rechts."""
    werte = list(werte)[-breite:] if breite > 0 else []
    lo, hi = SKALA
    stufen = hoehe * 8
    pegel = [max(1, min(stufen, round((v - lo) / (hi - lo) * stufen))) for v in werte]
    t = Text()
    for zeile in range(hoehe - 1, -1, -1):
        t.append(" " * (breite - len(werte)))
        for v, p in zip(werte, pegel):
            rest = p - zeile * 8
            t.append(BLOECKE[max(0, min(8, rest))], style=FARBE[c.bewertung_signal(v)[1]])
        if zeile:
            t.append("\n")
    return t


def styl(text, stufe):
    return Text(str(text), style=FARBE.get(stufe, stufe) or "")


def zahl(v, suffix=""):
    return "–" if v in (None, "") else f"{v}{suffix}"


def video_zelle(r, richtung):
    """'Jitter/Verlust' des UDP-Tests, gefärbt nach dem schlechteren der beiden Werte."""
    j, v = c.num(r.get(f"udp_{richtung}_jitter_ms")), c.num(r.get(f"udp_{richtung}_verlust_pct"))
    if j is None:
        return "–"
    stufen = [c.stufe_udp_jitter(j), c.stufe_udp_loss(v)]
    st = next((s for s in ("schlecht", "mittel", "gut") if s in stufen), "")
    return styl(f"{zahl(r.get(f'udp_{richtung}_jitter_ms'))}/{zahl(r.get(f'udp_{richtung}_verlust_pct'))}", st)


def ergebnis_text(r, aps):
    """Rich-Text mit der Zusammenfassung einer Messung."""
    s = c.num(r.get("signal_avg"))
    txt, st = c.bewertung_signal(s)
    loss, rq = c.num(r.get("ping_verlust_pct")), c.num(r.get("retry_quote_pct"))
    t = Text()
    t.append(f"{r['ort']}", style="bold")
    t.append(f"  ·  {r.get('szenario', '')}  ·  {str(r.get('zeit', ''))[:16].replace('T', ' ')}\n", style="dim")
    t.append(f"AP          {r.get('ap') or '–'}  ({c.kanal_name(c.num(r.get('freq_mhz')))})")
    if int(c.num(r.get("roams")) or 0):
        t.append(f"   ⚠ {int(c.num(r['roams']))}× AP gewechselt", style=FARBE["mittel"])
    t.append("\nSignal      ")
    t.append(f"{zahl(r.get('signal_avg'), ' dBm')} ({txt})", style=FARBE[st])
    t.append(f"   min {zahl(r.get('signal_min'))} / max {zahl(r.get('signal_max'))}\n")
    t.append(f"Bitrate     TX {zahl(r.get('tx_bitrate_avg'))}  RX {zahl(r.get('rx_bitrate_avg'))} Mbit/s\n")
    t.append("Ping        Verlust ")
    t.append(zahl(r.get("ping_verlust_pct"), " %"), style=FARBE[c.stufe_loss(loss)])
    t.append(f"   RTT {zahl(r.get('rtt_avg'))} ms (max {zahl(r.get('rtt_max'))})   Jitter {zahl(r.get('jitter_ms'))} ms\n")
    t.append("WLAN-Ebene  Retries ")
    t.append(zahl(r.get("retry_quote_pct"), " %"), style=FARBE[c.stufe_retry(rq)])
    t.append(f"   TX failed {zahl(r.get('tx_failed'))}   Beacon loss {zahl(r.get('beacon_loss'))}")
    pk = c.num(r.get("tx_packets"))
    if pk is not None and pk < 500:
        t.append(f"   (nur {int(pk)} Pakete – unsicher" + ("" if r.get("last_retry_pct") else ", mit iperf messen")
                 + ")", style="dim")
    if r.get("last_retry_pct") not in (None, ""):
        t.append("\nUnter Last  Retries ")
        t.append(zahl(r.get("last_retry_pct"), " %"), style=FARBE[c.stufe_retry(c.num(r.get("last_retry_pct")))])
        t.append(f"   TX failed {zahl(r.get('last_tx_failed'))}   ({zahl(r.get('last_tx_packets'))} Pakete beim Upload)")
    if r.get("iperf_down_mbit") or r.get("iperf_up_mbit"):
        t.append(f"\niperf3      ↓ {zahl(r.get('iperf_down_mbit'))}  ↑ {zahl(r.get('iperf_up_mbit'))} Mbit/s", style="bold")
    for richtung, pfeil in (("up", "↑"), ("down", "↓")):
        j, v = c.num(r.get(f"udp_{richtung}_jitter_ms")), c.num(r.get(f"udp_{richtung}_verlust_pct"))
        if j is None:
            continue
        t.append(f"\nVideo {pfeil} {c.UDP_MBIT} Mbit/s  Jitter ")
        t.append(zahl(r.get(f"udp_{richtung}_jitter_ms"), " ms"), style=FARBE[c.stufe_udp_jitter(j)])
        t.append("   Verlust ")
        t.append(zahl(r.get(f"udp_{richtung}_verlust_pct"), " %"), style=FARBE[c.stufe_udp_loss(v)])
    if r.get("iperf_fehler"):
        t.append(f"\n⚠ Durchsatztest fehlgeschlagen: {r['iperf_fehler']}", style=FARBE["schlecht"])
    if r.get("notiz"):
        t.append(f"\nNotiz       {r['notiz']}", style="italic")
    w = c.klebe_warnung(aps)
    if w:
        t.append(f"\n⚠ {w}", style=FARBE["mittel"])
    return t


def fuelle_ap_tabelle(table, aps):
    table.clear(columns=True)
    table.add_columns("", "AP", "Band / Kanal", "Signal", "BSSID")
    for a in aps:
        s = c.num(a.get("signal_dbm"))
        table.add_row("●" if a.get("verbunden") else "", a.get("ap") or a["bssid"],
                      c.kanal_name(c.num(a.get("freq_mhz"))),
                      styl(zahl(s, " dBm") + ("" if a.get("quelle") == "iw" else " ≈"), c.bewertung_signal(s)[1]),
                      a["bssid"])


# --------------------------------------------------------------------------- Dialoge

class Bestaetigen(ModalScreen[bool]):
    BINDINGS = [("escape", "dismiss(False)", "Abbrechen")]

    def __init__(self, frage):
        super().__init__()
        self.frage = frage

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self.frage)
            with Horizontal(classes="knoepfe"):
                yield Button("Löschen", variant="error", id="ja")
                yield Button("Abbrechen", id="nein")

    @on(Button.Pressed)
    def antwort(self, e: Button.Pressed):
        self.dismiss(e.button.id == "ja")


class Details(ModalScreen):
    BINDINGS = [("escape", "dismiss", "Schließen"), ("q", "dismiss", "Schließen")]

    def __init__(self, r):
        super().__init__()
        self.r = r

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog breit"):
            aps = c.lade_scan(self.r["id"])
            for a in aps:
                a["ap"] = c.ap_name(a["bssid"])
            yield Static(ergebnis_text(self.r, aps))
            if aps:
                yield DataTable(id="detail_aps", cursor_type="none")
            else:
                yield Static(Text("\nBei dieser Messung lief kein AP-Scan.", style="dim"))
            yield Button("Schließen", id="zu")

    def on_mount(self):
        aps = c.lade_scan(self.r["id"])
        for a in aps:
            a["ap"] = c.ap_name(a["bssid"])
        if aps:
            fuelle_ap_tabelle(self.query_one("#detail_aps", DataTable), aps)

    @on(Button.Pressed, "#zu")
    def zu(self):
        self.dismiss()


# --------------------------------------------------------------------------- App

class WifiSurvey(App):
    TITLE = "WLAN-Vermessung"
    CSS = """
    #status { height: 1; padding: 0 1; background: $panel; color: $foreground; }
    TabbedContent, ContentSwitcher { height: 1fr; }
    TabPane { padding: 0 1; height: 1fr; }
    TabPane > Horizontal { height: 1fr; }
    .formular Button { min-width: 5; }
    #ort_add, #ort_hoch, #ort_runter { width: 5; }
    #links { width: 34; margin-right: 1; }
    #orte { height: 1fr; }
    .titel { color: $primary; text-style: bold; margin-top: 1; }
    .hinweis { color: $text-muted; }
    #optionen { height: auto; }
    #optionen Input { width: 1fr; }
    #dauer { max-width: 12; }
    #iperf_test { min-width: 8; width: 8; margin-right: 1; }
    #steuerung { height: auto; margin: 1 0 0 0; }
    #steuerung Button { margin-right: 1; }
    #phase { padding: 1 1; color: $text-muted; }
    #fortschritt { margin: 1 0 0 0; }
    #spark { height: 4; margin: 0 0 0 0; }
    #live { height: auto; }
    #ergebnis_box { height: 1fr; margin-top: 1; border: round $panel; padding: 0 1; }
    #ap_tabelle { height: auto; max-height: 12; margin-top: 1; }
    .leiste { height: auto; margin-bottom: 1; }
    .leiste Select { width: 40; margin-right: 2; }
    .leiste Checkbox { margin-right: 2; }
    #report, #vergleich, #sz_tabelle { height: 1fr; }
    #vg_info, #rep_info { height: auto; max-height: 12; margin-top: 1; }
    .formular { height: auto; margin-top: 1; }
    .formular Input { width: 1fr; }
    .formular Button { margin-left: 1; }
    #sz_name { max-width: 30; }
    #ort_spalte, #ap_spalte { width: 1fr; }
    #ort_spalte { margin-right: 2; }
    #orte_edit { height: 1fr; }
    #aps_tab { height: 1fr; }
    Bestaetigen, Details { align: center middle; }
    .dialog { width: 60; height: auto; padding: 1 2; border: thick $primary; background: $surface; }
    .dialog.breit { width: 100; max-height: 90%; }
    .knoepfe { height: auto; margin-top: 1; }
    .knoepfe Button { margin-right: 1; }
    #detail_aps { height: auto; max-height: 14; margin: 1 0; }
    """
    BINDINGS = [
        Binding("f5", "messen", "Messen"),
        Binding("escape", "abbrechen", "Abbrechen", show=False),
        Binding("1", "tab('messen')", "Messen", show=False),
        Binding("2", "tab('uebersicht')", "Übersicht", show=False),
        Binding("3", "tab('vergleich_tab')", "Vergleich", show=False),
        Binding("4", "tab('szenarien')", "Szenarien", show=False),
        Binding("5", "tab('orte_aps')", "Orte & APs", show=False),
        Binding("delete", "loeschen", "Löschen"),
        Binding("ctrl+r", "neu_laden", "Neu laden"),
        Binding("ctrl+q", "quit", "Beenden"),
    ]

    def __init__(self):
        super().__init__()
        self.cfg = c.lade_config()
        self.iface = c.wlan_interface()
        self.verlauf = deque(maxlen=400)
        self.stop = threading.Event()
        self.misst = False
        self._orte = []
        self._theme_mtime = None
        self._letzte_aps = []

    # ---------------------------------------------------------------- Aufbau
    def compose(self) -> ComposeResult:
        yield Static("", id="status")
        with TabbedContent(initial="messen"):
            with TabPane("1 Messen", id="messen"):
                with Horizontal():
                    with Vertical(id="links"):
                        yield Label("", id="sz_label", classes="titel")
                        yield OptionList(id="orte")
                        yield Label("Enter/F5 startet die Messung", classes="hinweis")
                    with Vertical():
                        with Horizontal(id="optionen"):
                            yield Input(placeholder="Notiz (z.B. Tür zu)", id="notiz")
                            yield Input(str(self.cfg["dauer"]), placeholder="Sek.", type="integer", id="dauer")
                            yield Input(self.cfg["iperf_host"], placeholder="iperf3-Server (IP, optional)", id="iperf")
                            yield Button("Test", id="iperf_test", tooltip="iperf3-Server kurz testen (3 s je Richtung)")
                            yield Checkbox("AP-Scan", self.cfg["scan"], id="scan")
                            yield Checkbox("Video-Test", self.cfg["udp"], id="udp",
                                           tooltip=f"UDP-Strom {c.UDP_MBIT} Mbit/s je Richtung über iperf3 "
                                                   f"({c.UDP_DAUER} s), misst Jitter und Verlust wie im Videocall")
                        with Horizontal(id="steuerung"):
                            yield Button("▶ Messung starten", variant="primary", id="start")
                            yield Button("■ Abbrechen", variant="error", id="stop", disabled=True)
                            yield Static("", id="phase")
                        yield ProgressBar(total=100, show_eta=False, id="fortschritt")
                        yield Label(f"Signalverlauf (1 Balken = 1 s, Skala {SKALA[0]} … {SKALA[1]} dBm)",
                                    classes="titel")
                        yield Static("", id="spark")
                        yield Static("", id="live")
                        with VerticalScroll(id="ergebnis_box"):
                            yield Static("Noch keine Messung in dieser Sitzung.", id="ergebnis", classes="hinweis")
                            yield DataTable(id="ap_tabelle", cursor_type="none")
            with TabPane("2 Übersicht", id="uebersicht"):
                with Horizontal(classes="leiste"):
                    yield Select([("Alle Szenarien", ALLE)], value=ALLE, id="rep_sz", allow_blank=False)
                    yield Checkbox("alle Messungen (nicht nur letzte je Ort)", id="rep_alle")
                yield DataTable(id="report", cursor_type="row", zebra_stripes=True)
                yield Static("Enter = Details · Entf = Messung löschen", id="rep_info", classes="hinweis")
            with TabPane("3 Vergleich", id="vergleich_tab"):
                with Horizontal(classes="leiste"):
                    yield Select([(v[1] + f" ({v[3]})", k) for k, v in c.METRIKEN.items()],
                                 value="signal", allow_blank=False, id="vg_metrik")
                yield DataTable(id="vergleich", cursor_type="row", zebra_stripes=True)
                yield Static("", id="vg_info")
            with TabPane("4 Szenarien", id="szenarien"):
                yield Label("Ein Szenario = ein Aufbau deiner Netzwerkgeräte. "
                            "Enter aktiviert, neue Messungen werden dem aktiven Szenario zugeordnet.",
                            classes="hinweis")
                yield DataTable(id="sz_tabelle", cursor_type="row", zebra_stripes=True)
                with Horizontal(classes="formular"):
                    yield Input(placeholder="Name", id="sz_name")
                    yield Input(placeholder="Beschreibung: Geräte, Standorte, Kabel/Funk, Kanäle …", id="sz_beschr")
                    yield Button("Speichern", id="sz_speichern")
                    yield Button("Aktivieren", variant="primary", id="sz_aktiv")
            with TabPane("5 Orte & APs", id="orte_aps"):
                with Horizontal():
                    with Vertical(id="ort_spalte"):
                        yield Label("Messorte", classes="titel")
                        yield OptionList(id="orte_edit")
                        with Horizontal(classes="formular"):
                            yield Input(placeholder="Neuer Ort", id="ort_neu")
                            yield Button("+", id="ort_add")
                            yield Button("↑", id="ort_hoch")
                            yield Button("↓", id="ort_runter")
                            yield Button("Entfernen", variant="error", id="ort_weg")
                    with Vertical(id="ap_spalte"):
                        yield Label("Access Points (Name gilt für 2,4 und 5 GHz eines Geräts)", classes="titel")
                        yield DataTable(id="aps_tab", cursor_type="row", zebra_stripes=True)
                        with Horizontal(classes="formular"):
                            yield Input(placeholder="Name für markierten AP", id="ap_name")
                            yield Button("Benennen", id="ap_benennen")
                            yield Button("Scannen", id="ap_scan")
        yield Footer()

    def on_mount(self):
        self.sub_title = f"v{__version__} · Daten: {c.DATA}"
        self.theme_pruefen()
        self.set_interval(3, self.theme_pruefen)
        self.set_interval(1, self.live_tick)
        self.alles_laden()
        self.query_one("#orte").focus()
        if not c.aktives_szenario():
            self.query_one(TabbedContent).active = "szenarien"
            self.query_one("#sz_name").focus()
            self.notify("Lege zuerst ein Szenario an – z.B. „Ist-Zustand“ mit kurzer Beschreibung deines Aufbaus.",
                        title="Willkommen", timeout=10)

    def theme_pruefen(self):
        try:
            mt = THEME_DATEI.stat().st_mtime
        except OSError:
            return
        if mt != self._theme_mtime:
            self._theme_mtime = mt
            th = lade_omarchy_theme()
            if th:
                self.register_theme(th)
                self.theme = "omarchy"
                if self._orte:  # Tabellen mit neuen Ampelfarben neu zeichnen
                    self.alles_laden()

    # ---------------------------------------------------------------- Laden
    def alles_laden(self):
        self.lade_orte_liste()
        self.lade_report_auswahl()
        self.lade_report()
        self.lade_vergleich()
        self.lade_szenarien()
        self.lade_orte_edit()
        self.lade_aps()

    def action_neu_laden(self):
        self.cfg = c.lade_config()
        self.alles_laden()
        self.notify("Neu geladen")

    def lade_orte_liste(self):
        sz = c.aktives_szenario()
        anz = c.anzahl_messungen()
        self._orte = c.lade_orte()
        ol = self.query_one("#orte", OptionList)
        idx = ol.highlighted
        ol.clear_options()
        for o in self._orte:
            n = anz.get((sz, o), 0)
            ol.add_option(Option(Text.assemble(("✓ " if n else "  ", FARBE["gut"]), o, (f"  {n}×" if n else "", "dim"))))
        if self._orte:
            ol.highlighted = min(idx or 0, len(self._orte) - 1)
        b = c.lade_szenarien().get(sz, "")
        self.query_one("#sz_label", Label).update(f"Szenario: {sz or '— keins —'}")
        self.query_one("#sz_label", Label).tooltip = b or None

    def lade_report_auswahl(self):
        sel = self.query_one("#rep_sz", Select)
        alt = sel.value if sel.value is not Select.BLANK else None
        opts = [("Alle Szenarien", ALLE)] + [(s, s) for s in c.lade_szenarien()]
        sel.set_options(opts)
        werte = [v for _, v in opts]
        sel.value = alt if alt in werte else (c.aktives_szenario() if c.aktives_szenario() in werte else ALLE)

    def lade_report(self):
        sel = self.query_one("#rep_sz", Select).value
        sz = None if sel in (ALLE, Select.BLANK) else sel
        rows = c.report_daten(sz, self.query_one("#rep_alle", Checkbox).value)
        t = self.query_one("#report", DataTable)
        t.clear(columns=True)
        spalten = (["Szenario"] if sz is None else []) + ["Ort", "Zeit", "AP", "Band", "dBm", "min", "TX", "RX",
                                                          "Loss %", "RTT", "Jitter", "Retry %", "Retry Last %",
                                                          "↓ iperf", "↑ iperf", "Video ↑ ms/%", "Video ↓ ms/%",
                                                          "Hinweis"]
        t.add_columns(*spalten)
        for r in rows:
            zellen = ([r.get("szenario") or "–"] if sz is None else []) + [
                r["ort"], r["zeit"][5:16].replace("T", " "), r["ap"], r["band"],
                styl(zahl(r["signal_avg"]), c.bewertung_signal(c.num(r["signal_avg"]))[1]),
                zahl(r["signal_min"]), zahl(r["tx_bitrate_avg"]), zahl(r["rx_bitrate_avg"]),
                styl(zahl(r.get("ping_verlust_pct")), c.stufe_loss(c.num(r.get("ping_verlust_pct")))),
                zahl(r.get("rtt_avg")), zahl(r.get("jitter_ms")),
                styl(zahl(r.get("retry_quote_pct")), c.stufe_retry(c.num(r.get("retry_quote_pct")))),
                styl(zahl(r.get("last_retry_pct")), c.stufe_retry(c.num(r.get("last_retry_pct")))),
                zahl(r.get("iperf_down_mbit")), zahl(r.get("iperf_up_mbit")),
                video_zelle(r, "up"), video_zelle(r, "down"), r["hinweis"]]
            t.add_row(*zellen, key=r["id"])
        self._report_rows = {r["id"]: r for r in rows}

    def lade_vergleich(self):
        metrik = self.query_one("#vg_metrik", Select).value
        d = c.vergleich_daten(metrik if metrik is not Select.BLANK else "signal")
        t = self.query_one("#vergleich", DataTable)
        t.clear(columns=True)
        info = self.query_one("#vg_info", Static)
        if not d["zeilen"]:
            info.update(Text("Noch keine Werte für diese Kennzahl." +
                             (" Für Durchsatz einen iperf3-Server angeben." if metrik in ("down", "up") else ""),
                             style="dim"))
            return
        t.add_columns("Ort", *d["szenarien"])

        def zellen(z, fett=False):
            out = []
            for v, n, m in zip(z["werte"], z["n"], z["mark"]):
                s = "–" if v is None else f"{v:g}" + (f" ({n})" if n > 1 else "")
                out.append(Text(s, style=(FARBE.get(m, "") + (" bold" if fett else "")).strip(), justify="right"))
            return out
        for z in d["zeilen"]:
            t.add_row(z["ort"], *zellen(z))
        if d["zusammenfassung"]:
            t.add_row(*[Text("─" * 12, style="dim")] * (len(d["szenarien"]) + 1))
            for z in d["zusammenfassung"]:
                t.add_row(Text(z["ort"], style="bold"), *zellen(z, True))
        t.add_row(Text("Orte gewonnen", style="bold"),
                  *[Text(str(d["siege"][s]), style="bold", justify="right") for s in d["szenarien"]])
        txt = Text(f"{d['titel']} in {d['einheit']} – {'höher' if d['hoch_gut'] else 'niedriger'} ist besser. "
                   f"Unterschiede < {d['toleranz']:g} {d['einheit']} gelten als gleich. (n) = Anzahl Messungen.\n",
                   style="dim")
        if d["fehlend"]:
            txt.append("Noch nicht gemessen: ", style="bold")
            txt.append("; ".join(f"{s}: {', '.join(o)}" for s, o in d["fehlend"].items()) + "\n")
        for s in d["szenarien"]:
            if d["beschreibungen"].get(s):
                txt.append(f"{s}: ", style="bold")
                txt.append(d["beschreibungen"][s] + "\n")
        info.update(txt)

    def lade_szenarien(self):
        t = self.query_one("#sz_tabelle", DataTable)
        t.clear(columns=True)
        t.add_columns("", "Name", "Beschreibung", "Messungen", "Orte")
        anz, akt = c.anzahl_messungen(), c.aktives_szenario()
        for n, b in c.lade_szenarien().items():
            m = sum(v for (s, _), v in anz.items() if s == n)
            o = len({o for (s, o) in anz if s == n})
            t.add_row(Text("●", style=FARBE["gut"]) if n == akt else "", Text(n, style="bold" if n == akt else ""),
                      b, str(m), f"{o}/{len(c.lade_orte())}", key=n)

    def lade_orte_edit(self):
        ol = self.query_one("#orte_edit", OptionList)
        idx = ol.highlighted
        ol.clear_options()
        orte = c.lade_orte()
        ol.add_options(orte)
        if orte:
            ol.highlighted = min(idx or 0, len(orte) - 1)

    def lade_aps(self):
        """Bekannte APs: benannte aus aps.txt + alle BSSIDs aus Scans."""
        namen = c.lade_aps()
        gesehen = {}
        for a in c.read_csv(c.SCANS) + self._letzte_aps:
            b = a["bssid"].lower()
            gesehen[b] = {**gesehen.get(b, {}), **{k: v for k, v in a.items() if v not in (None, "")}}
        t = self.query_one("#aps_tab", DataTable)
        t.clear(columns=True)
        t.add_columns("Name", "BSSID", "Band / Kanal", "zuletzt dBm")
        geraete = {}
        for b, a in sorted(gesehen.items()):
            geraete.setdefault(":".join(b.split(":")[:5]), []).append(a)
        for p in namen:
            geraete.setdefault(p, [])
        for p, zellen in geraete.items():
            name = c.ap_name(p + ":00", namen) if any(p.startswith(k) or k.startswith(p) for k in namen) else ""
            if not zellen:
                t.add_row(Text(name, style="bold"), p + ":xx", "–", "–", key=p + ":xx")
            for a in zellen:
                s = c.num(a.get("signal_dbm"))
                t.add_row(Text(name or "(unbenannt)", style="bold" if name else "dim"), a["bssid"],
                          c.kanal_name(c.num(a.get("freq_mhz"))),
                          styl(zahl(s), c.bewertung_signal(s)[1]), key=a["bssid"])

    # ---------------------------------------------------------------- Live
    def live_tick(self):
        if self.iface:
            self.live_lesen()

    @work(thread=True, exclusive=True, group="live")
    def live_lesen(self):
        st = c.station(self.iface)
        ssid, freq = c.link(self.iface)
        self.call_from_thread(self.live_zeigen, st, ssid, freq)

    def live_zeigen(self, st, ssid, freq):
        status = self.query_one("#status", Static)
        sz = c.aktives_szenario() or "kein Szenario"
        t = Text.assemble(("🧪 ", ""), (sz, "bold"), "   ")
        if not st:
            t.append("📶 nicht verbunden", style=FARBE["schlecht"])
            status.update(t)
            return
        s = st["signal"]
        self.verlauf.append(s)
        t.append(f"📶 {ssid} · {c.ap_name(st['bssid'])} · {c.kanal_name(freq)} · ")
        t.append(f"{s:.0f} dBm", style="bold " + FARBE[c.bewertung_signal(s)[1]])
        t.append(f" · TX {st['tx_bitrate'] or 0:.0f} / RX {st['rx_bitrate'] or 0:.0f} Mbit/s")
        status.update(t)
        spark = self.query_one("#spark", Static)
        spark.update(signal_chart(self.verlauf, spark.size.width))
        if not self.misst:
            werte = list(self.verlauf)
            self.query_one("#live", Static).update(Text(
                f"jetzt {s:.0f} dBm · Ø {sum(werte) / len(werte):.1f} · min {min(werte):.0f} · max {max(werte):.0f} dBm",
                style="dim"))

    # ---------------------------------------------------------------- Messen
    @on(OptionList.OptionSelected, "#orte")
    def ort_gewaehlt(self):
        self.action_messen()

    @on(Button.Pressed, "#start")
    def start_gedrueckt(self):
        self.action_messen()

    @on(Button.Pressed, "#stop")
    def stop_gedrueckt(self):
        self.action_abbrechen()

    @on(Button.Pressed, "#iperf_test")
    def iperf_test_knopf(self):
        host = self.query_one("#iperf", Input).value.strip()
        if not host:
            self.notify("Erst die IP des iperf3-Servers eintragen.", severity="warning")
            return
        self.query_one("#iperf_test", Button).disabled = True
        self.notify(f"Teste {host} … (ca. 7 s)")
        self.iperf_test_worker(host)

    @work(thread=True, exclusive=True, group="iperftest")
    def iperf_test_worker(self, host):
        down, up, fehler = c.iperf_schnelltest(host)
        self.call_from_thread(self.iperf_test_fertig, host, down, up, fehler)

    def iperf_test_fertig(self, host, down, up, fehler):
        self.query_one("#iperf_test", Button).disabled = False
        if fehler:
            self.notify(fehler, title="iperf3-Test fehlgeschlagen", severity="error", timeout=10)
            return
        self.cfg["iperf_host"] = host
        c.speichere_config(self.cfg)
        self.notify(f"↓ {zahl(down)} Mbit/s   ↑ {zahl(up)} Mbit/s – Server gespeichert",
                    title=f"iperf3-Server {host} funktioniert", timeout=8)

    def action_tab(self, tab):
        self.query_one(TabbedContent).active = tab

    def action_abbrechen(self):
        if self.misst:
            self.stop.set()
            self.query_one("#phase", Static).update("Breche ab …")

    def action_messen(self):
        if self.misst:
            return
        sz = c.aktives_szenario()
        if not sz:
            self.notify("Bitte zuerst ein Szenario anlegen und aktivieren.", severity="warning")
            self.query_one(TabbedContent).active = "szenarien"
            return
        ol = self.query_one("#orte", OptionList)
        if ol.highlighted is None or not self._orte:
            self.notify("Bitte einen Ort auswählen.", severity="warning")
            return
        ort = self._orte[ol.highlighted]
        try:
            dauer = max(5, int(self.query_one("#dauer", Input).value or 30))
        except ValueError:
            dauer = 30
        iperf = self.query_one("#iperf", Input).value.strip()
        scan = self.query_one("#scan", Checkbox).value
        udp = self.query_one("#udp", Checkbox).value
        self.cfg.update(dauer=dauer, iperf_host=iperf, scan=scan, udp=udp)
        c.speichere_config(self.cfg)
        self.query_one(TabbedContent).active = "messen"
        self.misst = True
        self.stop.clear()
        self.query_one("#start", Button).disabled = True
        self.query_one("#stop", Button).disabled = False
        extra = ((20 + (2 * c.UDP_DAUER if udp else 0)) if iperf else 0) + (8 if scan else 0)
        self.query_one("#fortschritt", ProgressBar).update(total=dauer + extra, progress=0)
        self.query_one("#ergebnis", Static).update(Text(f"Messe „{ort}“ … Laptop ruhig halten.", style="dim"))
        self.query_one("#ap_tabelle", DataTable).clear(columns=True)
        self.messen_worker(sz, ort, self.query_one("#notiz", Input).value.strip(), dauer, iperf, scan, udp)

    @work(thread=True, exclusive=True, group="messung")
    def messen_worker(self, sz, ort, notiz, dauer, iperf, scan, udp):
        def sample(sek, st, ap):
            self.call_from_thread(self.mess_fortschritt, sek + 1, st, ap)

        def status(t):
            self.call_from_thread(self.mess_status, t)
        try:
            r, samples, aps = c.messung(sz, ort, notiz, dauer, iface=self.iface,
                                        ping_ziel=self.cfg.get("ping_ziel") or None, iperf_host=iperf or None,
                                        mit_scan=scan, mit_udp=udp, on_sample=sample, on_status=status, stop=self.stop)
        except c.Abgebrochen:
            self.call_from_thread(self.mess_ende, None, None, "Abgebrochen – nichts gespeichert.")
            return
        except Exception as e:  # noqa: BLE001
            self.call_from_thread(self.mess_ende, None, None, f"Fehler: {e}")
            return
        c.speichern(r, samples, aps)
        self.call_from_thread(self.mess_ende, r, aps, None)

    def mess_fortschritt(self, sek, st, ap):
        self.query_one("#fortschritt", ProgressBar).update(progress=sek)
        if st:
            s = st["signal"]
            t = Text.assemble((f"{sek:3}s  ", "dim"), (f"{s:.0f} dBm", "bold " + FARBE[c.bewertung_signal(s)[1]]),
                              f"   TX {st['tx_bitrate'] or 0:.0f} / RX {st['rx_bitrate'] or 0:.0f} Mbit/s   {ap}")
        else:
            t = Text(f"{sek:3}s  ⚠ keine Verbindung", style=FARBE["schlecht"])
        self.query_one("#live", Static).update(t)

    def mess_status(self, text):
        self.query_one("#phase", Static).update(text)
        pb = self.query_one("#fortschritt", ProgressBar)
        if text.startswith("iperf") or text.startswith("Scanne"):
            pb.advance(10 if text.startswith("iperf") else 0)

    def mess_ende(self, r, aps, fehler):
        self.misst = False
        self.query_one("#start", Button).disabled = False
        self.query_one("#stop", Button).disabled = True
        self.query_one("#phase", Static).update("")
        pb = self.query_one("#fortschritt", ProgressBar)
        if fehler:
            pb.update(progress=0)
            self.query_one("#ergebnis", Static).update(Text(fehler, style=FARBE["schlecht"]))
            self.notify(fehler, severity="warning")
            return
        pb.update(progress=pb.total)
        self.query_one("#ergebnis", Static).update(ergebnis_text(r, aps))
        fuelle_ap_tabelle(self.query_one("#ap_tabelle", DataTable), aps)
        self._letzte_aps = aps
        self.notify(f"{r['ort']}: {r['signal_avg']} dBm, {zahl(r.get('ping_verlust_pct'), ' %')} Verlust",
                    title="Messung gespeichert")
        if r.get("iperf_fehler"):
            self.notify(r["iperf_fehler"], title="Durchsatztest fehlgeschlagen", severity="error", timeout=10)
        self.query_one("#notiz", Input).value = ""
        ol = self.query_one("#orte", OptionList)
        naechster = (ol.highlighted or 0) + 1
        self.alles_laden()
        if naechster < len(self._orte):
            ol.highlighted = naechster  # gleich zum nächsten Ort weiter
        ol.focus()

    # ---------------------------------------------------------------- Übersicht
    @on(Select.Changed, "#rep_sz")
    @on(Checkbox.Changed, "#rep_alle")
    def report_filter(self):
        self.lade_report()

    @on(DataTable.RowSelected, "#report")
    def report_details(self, e: DataTable.RowSelected):
        r = self._report_rows.get(e.row_key.value)
        if r:
            self.push_screen(Details(r))

    def action_loeschen(self):
        aktiv = self.query_one(TabbedContent).active
        if aktiv != "uebersicht":
            return
        t = self.query_one("#report", DataTable)
        if not t.row_count:
            return
        mid = t.coordinate_to_cell_key(t.cursor_coordinate).row_key.value
        r = self._report_rows.get(mid)

        def weg(ok):
            if ok:
                c.messung_loeschen(mid)
                self.alles_laden()
                self.notify("Messung gelöscht")
        self.push_screen(Bestaetigen(f"Messung „{r['ort']}“ vom {r['zeit'].replace('T', ' ')} "
                                     f"({r.get('szenario')}) löschen?"), weg)

    # ---------------------------------------------------------------- Vergleich
    @on(Select.Changed, "#vg_metrik")
    def metrik_gewechselt(self):
        self.lade_vergleich()

    # ---------------------------------------------------------------- Szenarien
    @on(DataTable.RowHighlighted, "#sz_tabelle")
    def sz_markiert(self, e: DataTable.RowHighlighted):
        n = e.row_key.value
        self.query_one("#sz_name", Input).value = n
        self.query_one("#sz_beschr", Input).value = c.lade_szenarien().get(n, "")

    @on(DataTable.RowSelected, "#sz_tabelle")
    def sz_gewaehlt(self, e: DataTable.RowSelected):
        self.sz_aktivieren(e.row_key.value)

    @on(Button.Pressed, "#sz_speichern")
    @on(Input.Submitted, "#sz_beschr")
    @on(Input.Submitted, "#sz_name")
    def sz_speichern(self):
        name = self.query_one("#sz_name", Input).value.strip()
        if not name:
            self.notify("Name fehlt.", severity="warning")
            return
        name = c.szenario_speichern(name, self.query_one("#sz_beschr", Input).value.strip())
        if not c.aktives_szenario():
            c.szenario_aktivieren(name)
        self.alles_laden()
        self.notify(f"Szenario „{name}“ gespeichert")

    @on(Button.Pressed, "#sz_aktiv")
    def sz_aktiv_knopf(self):
        name = self.query_one("#sz_name", Input).value.strip()
        if name:
            c.szenario_speichern(name, self.query_one("#sz_beschr", Input).value.strip())
            self.sz_aktivieren(name)

    def sz_aktivieren(self, name):
        c.szenario_aktivieren(name)
        self.alles_laden()
        self.notify(f"Aktives Szenario: {name}")
        self.query_one(TabbedContent).active = "messen"
        self.query_one("#orte").focus()

    # ---------------------------------------------------------------- Orte
    @on(Button.Pressed, "#ort_add")
    @on(Input.Submitted, "#ort_neu")
    def ort_add(self):
        i = self.query_one("#ort_neu", Input)
        if i.value.strip():
            c.ort_hinzufuegen(i.value.strip())
            i.value = ""
            self.alles_laden()

    def _ort_index(self):
        return self.query_one("#orte_edit", OptionList).highlighted

    @on(Button.Pressed, "#ort_weg")
    def ort_weg(self):
        i, orte = self._ort_index(), c.lade_orte()
        if i is not None and i < len(orte):
            weg = orte.pop(i)
            c.speichere_orte(orte)
            self.alles_laden()
            self.notify(f"„{weg}“ entfernt – bisherige Messungen bleiben erhalten")

    @on(Button.Pressed, "#ort_hoch")
    @on(Button.Pressed, "#ort_runter")
    def ort_schieben(self, e: Button.Pressed):
        i, orte = self._ort_index(), c.lade_orte()
        j = (i - 1 if e.button.id == "ort_hoch" else i + 1) if i is not None else None
        if j is not None and 0 <= j < len(orte):
            orte[i], orte[j] = orte[j], orte[i]
            c.speichere_orte(orte)
            self.alles_laden()
            self.query_one("#orte_edit", OptionList).highlighted = j

    # ---------------------------------------------------------------- APs
    @on(DataTable.RowHighlighted, "#aps_tab")
    def ap_markiert(self, e: DataTable.RowHighlighted):
        name = c.ap_name(e.row_key.value.replace("xx", "00"))
        self.query_one("#ap_name", Input).value = "" if ":" in name else name

    @on(Button.Pressed, "#ap_benennen")
    @on(Input.Submitted, "#ap_name")
    def ap_benennen(self):
        t = self.query_one("#aps_tab", DataTable)
        name = self.query_one("#ap_name", Input).value.strip()
        if not t.row_count or not name:
            return
        bssid = t.coordinate_to_cell_key(t.cursor_coordinate).row_key.value.replace("xx", "00")
        c.ap_benennen(bssid, name)
        self.alles_laden()
        self.notify(f"{bssid[:14]}… heißt jetzt „{name}“")

    @on(Button.Pressed, "#ap_scan")
    def ap_scan_knopf(self):
        self.notify("Scanne …")
        self.ap_scan_worker()

    @work(thread=True, exclusive=True, group="scan")
    def ap_scan_worker(self):
        ssid, _ = c.link(self.iface)
        st = c.station(self.iface)
        aps = c.scan(self.iface, ssid, verbunden=st["bssid"] if st else None) if ssid else []
        self.call_from_thread(self.ap_scan_fertig, aps)

    def ap_scan_fertig(self, aps):
        self._letzte_aps = aps
        self.lade_aps()
        self.notify(f"{len(aps)} Funkzellen deines Netzes gefunden")


def run():
    WifiSurvey().run()
