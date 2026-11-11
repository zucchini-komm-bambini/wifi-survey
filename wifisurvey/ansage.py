"""Akustische Rückmeldung für Messungen aus der Ferne: Signaltöne und Sprachansagen.

Töne werden selbst erzeugt und über PipeWire/PulseAudio/ALSA abgespielt.
Für Sprache wird espeak-ng (oder ersatzweise spd-say) verwendet; fehlt beides,
gibt es nur die Töne.
"""

import math
import os
import queue
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import wave
from pathlib import Path

ANSAGE_INTERVALL = 5  # Sekunden zwischen den Restzeit-Ansagen


def _player():
    for p in ("pw-play", "paplay", "aplay"):
        if shutil.which(p):
            return p
    return None


def _sprache():
    if shutil.which("espeak-ng"):
        return ["espeak-ng", "-v", "de", "-s", "150"]
    if shutil.which("spd-say"):
        return ["spd-say", "-l", "de", "-w"]
    return None


def hinweis():
    """Text, falls Ton oder Sprache fehlen, sonst None."""
    if not _player():
        return "Kein Audio-Player gefunden (pw-play, paplay oder aplay) – keine Töne und Ansagen."
    if not _sprache():
        return "Keine Sprachausgabe installiert (sudo pacman -S espeak-ng) – es gibt nur Signaltöne."
    return None


def _ton_datei():
    ordner = Path(os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir())
    pfad = ordner / "wifi-survey-ton.wav"
    if pfad.exists():
        return pfad
    rate, freq, n = 44100, 880, int(44100 * 0.18)
    rampe = 600  # weiches Ein-/Ausblenden gegen Knacken
    frames = b"".join(
        struct.pack("<h", int(14000 * min(1, i / rampe, (n - i) / rampe) * math.sin(2 * math.pi * freq * i / rate)))
        for i in range(n))
    tmp = pfad.with_suffix(".tmp")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(frames)
    tmp.replace(pfad)
    return pfad


def ton():
    """Kurzer Signalton, kehrt sofort zurück."""
    p = _player()
    if p:
        subprocess.Popen([p, str(_ton_datei())], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class Ansager:
    """Spricht Texte nacheinander in einem Hintergrund-Thread und sagt die Restzeit an."""

    def __init__(self, countdown, intervall=ANSAGE_INTERVALL, name="Messung"):
        self.countdown = countdown
        self.intervall = intervall
        self.name = name
        self._cmd = _sprache()
        self._texte = queue.Queue()
        self._ende = None
        self._stop = threading.Event()
        threading.Thread(target=self._sprecher, daemon=True).start()

    # ---------------------------------------------------------------- Sprache
    def sage(self, text):
        if self._cmd:
            self._texte.put(text)

    def _sprecher(self):
        while True:
            text = self._texte.get()
            try:
                subprocess.run(self._cmd + [text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
            except (subprocess.TimeoutExpired, OSError):
                pass
            self._texte.task_done()

    def _warten(self, timeout=8):
        t0 = time.time()
        while self._texte.unfinished_tasks and time.time() - t0 < timeout:
            time.sleep(0.05)

    # ---------------------------------------------------------------- Ablauf
    def runterzaehlen(self, stop, on_status=None, abbruch=Exception):
        """Countdown; in den letzten drei Sekunden ein Ton pro Sekunde. Wirft `abbruch` bei stop."""
        ende = time.time() + self.countdown
        while (rest := ende - time.time()) > 0:
            n = math.ceil(rest)
            if on_status:
                on_status(f"Start in {n} s …")
            if n <= 3:
                ton()
            while time.time() < ende - (n - 1):
                if stop.is_set():
                    raise abbruch()
                time.sleep(0.05)

    def starten(self, rest):
        """'<Name> gestartet' und ab jetzt alle `intervall` Sekunden die Restzeit."""
        self.sage(f"{self.name} gestartet")
        self.rest(rest)
        threading.Thread(target=self._restzeit, daemon=True).start()

    def rest(self, sekunden):
        """Neue Schätzung der verbleibenden Messzeit (bei jedem Phasenwechsel)."""
        self._ende = time.time() + sekunden

    def _restzeit(self):
        t0, k = time.time(), 1
        while not self._stop.wait(max(0, t0 + k * self.intervall - time.time())):
            k += 1
            r = self._ende - time.time()
            if r < 3:
                continue  # gleich fertig, nur noch "… beendet"
            if not self._texte.unfinished_tasks:  # nichts überlappen lassen
                self.sage(restzeit_text(r, self.intervall))

    def beenden(self, text):
        """Restzeit-Ansagen stoppen, Abschlusstext sprechen und abwarten."""
        self._stop.set()
        while not self._texte.empty():  # veraltete Restzeit-Ansagen verwerfen
            try:
                self._texte.get_nowait()
                self._texte.task_done()
            except queue.Empty:
                break
        self.sage(text)
        self._warten()


def restzeit_text(r, intervall=ANSAGE_INTERVALL):
    """Restzeit als Ansage, auf das Ansage-Intervall gerundet, damit sie gleichmäßig herunterzählt."""
    if intervall >= 60 and r >= 90:
        m = int(round(r / 60))
        return f"noch {m} Minuten"
    if r >= 60 and intervall >= 60:
        return "noch eine Minute"
    r = int(round(r / ANSAGE_INTERVALL) * ANSAGE_INTERVALL) if r > 10 else int(round(r))
    return f"noch {r} Sekunden"


def mit_ansage(countdown, fn, abbruch, intervall=ANSAGE_INTERVALL, name="Messung"):
    """Führt fn(ansager) aus; mit countdown > 0 inklusive Countdown, Restzeit und Abschlussansage.

    fn bekommt None, wenn kein Countdown gewünscht ist (dann gibt es keine Ansagen)."""
    if not countdown:
        return fn(None)
    ans = Ansager(countdown, intervall, name)
    try:
        erg = fn(ans)
    except abbruch:
        ans.beenden(f"{name} abgebrochen")
        raise
    except Exception:
        ans.beenden(f"{name} fehlgeschlagen")
        raise
    ans.beenden(f"{name} beendet")
    return erg
