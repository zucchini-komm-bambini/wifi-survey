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
  deutlich stärker wäre („Client klebt am AP“)

Mit einem iperf3-Server im Netz zusätzlich:

- **Durchsatz** – TCP-Download und -Upload, je 10 s
- **Retries unter Last** – WLAN-Zähler während des Uploads; deutlich aussagekräftiger als
  die Retry-Quote der Ping-Phase, die nur auf wenigen hundert Paketen beruht
- **Video-Test** – gleichmäßiger UDP-Strom mit 3 Mbit/s und 1200-Byte-Paketen in beide
  Richtungen, wie bei einer Videokonferenz; misst Jitter und Verlust

## Installation

Abhängigkeiten: `python`, `python-textual`, `iw`, `iputils`, `iproute2`, `networkmanager`.
Optional: `iperf3` für Durchsatz und Video-Test, `gum` für hübschere Auswahlmenüs in der CLI.

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
wifi-survey report              # Übersicht des aktiven Szenarios
wifi-survey vergleich -w signal # Orte × Szenarien für eine Kennzahl
wifi-survey iperftest <IP>      # iperf3-Server testen und als Standard speichern
wifi-survey orte | aps | szenarien   # Listen im Editor bearbeiten
wifi-survey pfad                # Datenordner ausgeben
```

Kennzahlen für `vergleich -w`: `signal`, `min`, `loss`, `rtt`, `jitter`, `retry`,
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
| `scans.csv` | sichtbare APs je Messung |
| `orte.txt`, `aps.txt`, `szenarien.txt` | Messpunkte, AP-Namen, Szenarien |
| `config.json` | Einstellungen |

## Lizenz

MIT, siehe [LICENSE](LICENSE).
