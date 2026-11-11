# wifi-survey

WLAN an festen Messpunkten in der Wohnung vermessen und verschiedene Netzwerk-Setups
(„Szenarien“) miteinander vergleichen – zum Beispiel Router allein, mit Repeatern oder
ein zusätzlicher Access Point an verschiedenen Standorten. Mit Textoberfläche (TUI) und
Kommandozeile, gebaut für Arch Linux.

## Was gemessen wird

Pro Messung (Standard 30 s, Laptop ruhig halten):

- **Signal** – Minimum, Mittel, Maximum (dBm), jede Sekunde aufgezeichnet
- **Bitraten** – TX/RX laut Treiber
- **Ping** zum Gateway – Verlust, RTT, Jitter
- **WLAN-Zähler** – Retries, fehlgeschlagene Sendungen, Beacon-Verluste, Roaming
- **AP-Scan** – welche Access Points des eigenen Netzes sichtbar sind und ob ein anderer
  deutlich stärker wäre („Client klebt am AP“); zusätzlich werden alle Nachbarnetze mit
  Kanal und Bandbreite gespeichert, um die Kanalbelegung später nachvollziehen zu können

Mit einem iperf3-Server im Netz zusätzlich:

- **Durchsatz** – TCP-Download und -Upload, je 10 s
- **Retries unter Last** – WLAN-Zähler während des Uploads; deutlich aussagekräftiger als
  die Retry-Quote der Ping-Phase, die nur auf wenigen hundert Paketen beruht
- **Video-Test** – gleichmäßiger UDP-Strom mit 3 Mbit/s und 1200-Byte-Paketen in beide
  Richtungen, wie bei einer Videokonferenz; misst Jitter und Verlust

## Auswertung für Videokonferenzen

- **Videocall-Urteil** je Messung: ● gut, ◐ geht, ○ schlecht – mit Begründung. Maßgeblich ist das
  schlechteste Kriterium: Verlust und Jitter des Video-Tests (sonst des Pings), RTT und Ping-Spitzen,
  Durchsatz in beide Richtungen (unter 10 Mbit/s „geht“, unter 4 „schlecht“), AP-Wechsel und Signalreserve.
- **Empfehlung** (`wifi-survey empfehlung`, TUI-Tab 6): Orte × Szenarien als Ampel, Rangfolge der Setups,
  Orte ohne gutes Setup, bestes Setup je Ort und noch fehlende Messungen. Bei mehreren Messungen
  zählt die schlechteste.
- **Vergleich** einzelner Kennzahlen nutzt bei Wiederholungen den Median und zeigt die Spanne; überlappen
  sich die Spannen, gilt ein Unterschied als nicht belastbar (gelb).

## Verlässlicher messen

- **Vorab-Check** vor jeder Messung: Netzteil oder Akku, WLAN-Stromsparmodus, VPN, Bluetooth (bei
  2,4 GHz). Wird mitgespeichert und in Übersicht und Ergebnis angezeigt.
- **Dauertest** (`wifi-survey dauertest -m 15`, TUI-Knopf „⏱ Dauertest“): so lange wie ein echter Call
  Videostrom in beide Richtungen plus Ping, jede Sekunde einzeln ausgewertet. Findet Aussetzer,
  Verbindungsabbrüche, AP- und Kanalwechsel (z. B. nach Radarerkennung auf DFS-Kanälen).
  Vorzeitig beenden (■ bzw. Strg+C) wertet die bisherige Zeit aus, sofern mindestens 60 s gelaufen sind.
  Übersicht: `wifi-survey dauertests` und TUI-Tab 6.

## Funkumfeld

Aus den bei jeder Messung gespeicherten Nachbarnetzen (`netze.csv`):

- je Messung, wie viele fremde Netze sich mit dem genutzten Kanal überschneiden (Kanalblöcke
  aus Hauptkanal und Breite berechnet, ab −82 dBm),
- Kanalwechsel deiner eigenen APs seit der vorigen Messung,
- `wifi-survey kanaele` bzw. TUI-Tab 7: ruhigste Kanäle je Band, DFS gekennzeichnet.

## Countdown mit Ansage

Damit der Laptop ungestört messen kann, während man selbst woanders ist: Mit Countdown
startet die Messung erst nach einem Vorlauf. In den letzten drei Sekunden gibt es einen
Signalton pro Sekunde, dann die Ansage „Messung gestartet“, danach alle 5 Sekunden die
geschätzte Restzeit und am Ende „Messung beendet“.

- TUI: Feld „Countdown s“ (leer oder 0 = sofort starten)
- CLI: `wifi-survey messen -c 20`
- Standardwert: `"countdown"` in `config.json`

Ohne `espeak-ng` gibt es nur die Signaltöne.

## Installation

Abhängigkeiten: `python`, `python-textual`, `iw`, `iputils`, `iproute2`, `networkmanager`.
Optional: `iperf3` für Durchsatz und Video-Test, `gum` für hübschere Auswahlmenüs in der CLI,
`espeak-ng` für Sprachansagen beim Countdown.

```sh
git clone https://github.com/zucchini-komm-bambini/wifi-survey.git
cd wifi-survey
makepkg -si
```

Ohne Installation läuft es auch direkt aus dem Ordner: `./wifi-survey`

## Benutzung

```sh
wifi-survey                     # TUI starten
wifi-survey szenario            # aktives Szenario wählen oder anlegen
wifi-survey messen              # Messung auf der Kommandozeile
wifi-survey messen -o Küche --iperf 192.168.1.10
wifi-survey messen -c 20            # mit 20 s Countdown und Ansagen
wifi-survey report              # Übersicht des aktiven Szenarios
wifi-survey vergleich -w signal # Orte × Szenarien für eine Kennzahl
wifi-survey empfehlung         # Videocall-Ampel, bestes Setup je Ort, Lücken
wifi-survey dauertest -m 15    # 15-Minuten-Test am aktuellen Platz
wifi-survey dauertests         # bisherige Dauertests
wifi-survey kanaele            # Kanalbelegung und ruhigste Kanäle
wifi-survey iperftest <IP>      # iperf3-Server testen und als Standard speichern
wifi-survey orte | aps | szenarien   # Listen im Editor bearbeiten
wifi-survey pfad                # Datenordner ausgeben
```

Kennzahlen für `vergleich -w`: `video`, `signal`, `min`, `loss`, `rtt`, `jitter`, `retry`,
`retry_last`, `bitrate`, `down`, `up`, `udp_jitter`, `udp_loss`, `udp_jitter_down`,
`udp_loss_down`.

Auf dem iperf3-Server genügt `iperf3 -s`.

## Daten

Alles liegt als CSV und Textdateien in `~/.local/share/wifi-survey/`
(bzw. `$XDG_DATA_HOME/wifi-survey`, überschreibbar mit `WIFI_SURVEY_DIR`):

| Datei | Inhalt |
|---|---|
| `messungen.csv` | eine Zeile pro Messung mit allen Kennzahlen |
| `samples.csv` | Sekundenwerte jeder Messung |
| `scans.csv` | sichtbare APs des eigenen Netzes je Messung |
| `netze.csv` | alle sichtbaren Netze inkl. Nachbarn mit Kanal und Bandbreite je Messung |
| `orte.txt`, `aps.txt`, `szenarien.txt` | Messpunkte, AP-Namen, Szenarien |
| `dauertests.csv` | Zusammenfassung je Dauertest |
| `dauertest_sekunden.csv` | Sekundenwerte der Dauertests |
| `config.json` | Einstellungen |

## Lizenz

MIT, siehe [LICENSE](LICENSE).
