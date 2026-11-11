"""Tests ohne WLAN und ohne iperf3-Server:  python -m unittest discover -s tests -t .

Die Ausgaben von iw, nmcli, ping und iperf3 liegen als aufgezeichnete Beispiele in tests/daten
(MAC-Adressen und Netznamen sind erfunden). Der Datenordner wird auf einen temporären Ordner
umgebogen, bevor wifisurvey geladen wird – die echten Messungen bleiben unberührt.
"""

import atexit
import os
import shutil
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="wifi-survey-test-"))
os.environ["WIFI_SURVEY_DIR"] = str(_TMP)
atexit.register(shutil.rmtree, _TMP, ignore_errors=True)

DATEN = Path(__file__).parent / "daten"


def text(name):
    return (DATEN / name).read_text()


def leeren():
    """Leert den Test-Datenordner (und nur den)."""
    from wifisurvey import core
    assert core.DATA == _TMP, f"Tests würden in {core.DATA} schreiben"
    for p in _TMP.iterdir():
        p.unlink()
