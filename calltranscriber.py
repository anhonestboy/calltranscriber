#!/usr/bin/env python3
"""
CallTranscriber — macOS menu bar app.
Drop video → extract audio → transcribe with whisper-cpp → output/.

Auto-downloads whisper model on first run. Zero API keys, all on-device.
"""
import os, sys, subprocess, time, json, signal, shutil, hashlib
from pathlib import Path
from datetime import datetime
from threading import Thread, Lock
from urllib.request import urlopen, Request

import rumps
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# ── CONSTANTS ────────────────────────────────────
APP_NAME = "CallTranscriber"
WHISPER_MODEL = "large-v3-turbo"  # best italiano, ~1.5 GB
WHISPER_LANG = "it"
MODEL_URL = f"https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-{WHISPER_MODEL}.bin"
MODEL_SHA256_URL = f"{MODEL_URL}.sha256"
MODEL_DIR = Path.home() / ".calltranscriber" / "models"
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".m4v"}

QUEUE: list[Path] = []
QUEUE_LOCK = Lock()
PROCESSING = False
LOG_LINES: list[str] = []


# ── UTILS ────────────────────────────────────────
def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    LOG_LINES.append(line)
    print(line)
    if len(LOG_LINES) > 200:
        del LOG_LINES[: len(LOG_LINES) - 200]


def find_binary(names: list[str]) -> str | None:
    # Aggiungi percorsi Homebrew al PATH (Finder non li eredita)
    os.environ["PATH"] = os.environ.get("PATH", "/usr/bin:/bin") + \
        ":/opt/homebrew/bin:/usr/local/bin"

    for name in names:
        # Prova brew --prefix con path assoluti
        for brew_bin in ["/opt/homebrew/bin/brew", "/usr/local/bin/brew", "brew"]:
            r = subprocess.run([brew_bin, "--prefix", name], capture_output=True, text=True)
            if r.returncode == 0:
                prefix = r.stdout.strip()
                for variant in [name, name.replace("-cpp", ""), name.replace("-cli", ""), "whisper-cli"]:
                    candidate = os.path.join(prefix, "bin", variant)
                    if os.path.isfile(candidate):
                        return candidate
                break  # prefix trovato, non provare altri brew_bin
        # shutil.which
        p = shutil.which(name)
        if p:
            return p
    return None


def model_path() -> Path:
    return MODEL_DIR / f"ggml-{WHISPER_MODEL}.bin"


def download_file(url: str, dest: Path, label: str = "") -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        req = Request(url, headers={"User-Agent": "CallTranscriber/1.0"})
        with urlopen(req) as resp, open(dest, "wb") as f:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            while True:
                chunk = resp.read(8192)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if total and downloaded % (5 * 1024 * 1024) == 0:
                    pct = downloaded * 100 // total
                    print(f"\r   {label}: {pct}% ({downloaded // 1024 // 1024} MB)", end="")
            if total:
                print(f"\r   {label}: 100% ({downloaded // 1024 // 1024} MB)")
        return True
    except Exception as e:
        log(f"❌ Download fallito: {e}")
        return False


def verify_sha256(path: Path, sha256_url: str) -> bool:
    try:
        req = Request(sha256_url, headers={"User-Agent": "CallTranscriber/1.0"})
        with urlopen(req) as resp:
            expected = resp.read().decode().strip()
        with open(path, "rb") as f:
            actual = hashlib.sha256(f.read()).hexdigest()
        return actual == expected
    except Exception:
        return False  # se non riesce a verificare, continua comunque


def ensure_model() -> Path | None:
    mp = model_path()
    if mp.exists():
        return mp

    log(f"📦 Scarico modello {WHISPER_MODEL} (~1.5 GB, solo prima volta)...")
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    tmp = mp.with_suffix(".tmp")

    if not download_file(MODEL_URL, tmp, f"ggml-{WHISPER_MODEL}.bin"):
        tmp.unlink(missing_ok=True)
        return None

    # Verifica checksum (best-effort)
    if verify_sha256(tmp, MODEL_SHA256_URL):
        log("   ✓ Checksum verificato")
    else:
        log("   ⚠️ Checksum non verificato — continuo comunque")

    tmp.rename(mp)
    log("   ✓ Modello scaricato")
    return mp


# ── AUDIO/VIDEO PROCESSING ───────────────────────
def extract_audio(video: Path, audio: Path, ffmpeg_bin: str) -> bool:
    log(f"🎵 Estrazione audio: {video.name}")
    cmd = [
        ffmpeg_bin, "-y", "-i", str(video),
        "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
        str(audio)
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        log(f"❌ ffmpeg: {r.stderr[-300:]}")
        return False
    return True


def transcribe(audio: Path, text: Path, whisper_bin: str, model: Path) -> bool:
    log(f"📝 Trascrizione in corso ({WHISPER_MODEL})...")
    cmd = [
        whisper_bin,
        "-m", str(model),
        "-f", str(audio),
        "-l", WHISPER_LANG,
        "-otxt",
        "-of", str(text.with_suffix("")),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
    if r.returncode != 0:
        log(f"❌ whisper: {r.stderr[-300:]}")
        return False
    # whisper-cli output: audio.wav.txt → rinomina
    whisper_out = Path(str(text.with_suffix("")) + ".txt")
    if whisper_out.exists() and whisper_out != text:
        whisper_out.rename(text)
    return True


def compress_video(src: Path, dst: Path, ffmpeg_bin: str) -> bool:
    log("🎬 Compressione video...")
    cmd = [
        ffmpeg_bin, "-y", "-i", str(src),
        "-c:v", "hevc_videotoolbox", "-b:v", "5M",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        "-tag:v", "hvc1",
        str(dst)
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode == 0


def process_video(video: Path, output_dir: Path, ffmpeg_bin: str, whisper_bin: str,
                  model: Path, compress: bool, delete_original: bool,
                  on_start=None, on_done=None):
    global PROCESSING
    base = video.stem
    audio_path = output_dir / f"{base}_audio.wav"
    text_path = output_dir / f"{base}_trascrizione.txt"
    compressed_path = output_dir / f"{base}_compressed.mp4"

    if on_start:
        on_start()

    if not extract_audio(video, audio_path, ffmpeg_bin):
        PROCESSING = False
        if on_done:
            on_done(video.name, False)
        return
    log(f"   → {audio_path.name}")

    if not transcribe(audio_path, text_path, whisper_bin, model):
        PROCESSING = False
        if on_done:
            on_done(video.name, False)
        return
    log(f"   → {text_path.name}")

    if compress:
        if compress_video(video, compressed_path, ffmpeg_bin):
            log(f"   → {compressed_path.name}")
        else:
            log("⚠️ Compressione fallita (non bloccante)")

    if delete_original and compress and compressed_path.exists():
        video.unlink()
        log("🗑️ Originale rimosso")

    log(f"✅ Completato: {video.name}")
    PROCESSING = False
    if on_done:
        on_done(video.name, True)


def queue_worker(ffmpeg_bin: str, whisper_bin: str, model: Path,
                 output_dir: Path, compress: bool, delete_original: bool,
                 on_start=None, on_done=None):
    global PROCESSING
    while True:
        with QUEUE_LOCK:
            if QUEUE and not PROCESSING:
                path = QUEUE.pop(0)
                PROCESSING = True
            else:
                path = None
        if path:
            try:
                process_video(path, output_dir, ffmpeg_bin, whisper_bin,
                            model, compress, delete_original,
                            on_start=on_start, on_done=on_done)
            except Exception as e:
                log(f"💥 Errore: {e}")
                PROCESSING = False
                if on_done:
                    on_done(path.name, False)
        else:
            time.sleep(1)


class VideoHandler(FileSystemEventHandler):
    def on_created(self, event):
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix.lower() not in VIDEO_EXTS:
            return
        if path.name.startswith("."):
            return
        Thread(target=self._delayed_enqueue, args=(path,), daemon=True).start()

    def _delayed_enqueue(self, path: Path, delay: float = 5.0):
        if not path.exists():
            return
        time.sleep(delay)
        if not path.exists():
            return
        with QUEUE_LOCK:
            if path not in QUEUE:
                QUEUE.append(path)
                log(f"📥 Nuovo: {path.name}")


# ── ICONS ────────────────────────────────────────
def icon_dir() -> Path:
    return Path.home() / ".calltranscriber"


def resource_path(filename: str) -> Path:
    """Path a un file di risorsa (funziona sia in dev che in PyInstaller)."""
    if getattr(sys, "frozen", False):
        # PyInstaller bundle
        base = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    else:
        base = Path(__file__).parent
    return base / filename


def ensure_icons() -> tuple[Path, Path]:
    """Copia le icone nella user dir se non esistono. Ritorna (idle, processing)."""
    d = icon_dir()
    d.mkdir(parents=True, exist_ok=True)
    idle = d / "icon.png"
    processing = d / "icon_processing.png"

    for name, dest in [("icon.png", idle), ("icon_processing.png", processing)]:
        try:
            src = resource_path(name)
            if src.exists():
                # Copia sempre — così gli aggiornamenti alle icone vengono applicati
                shutil.copy2(src, dest)
            else:
                log(f"⚠️ Icona {name} non trovata nel bundle")
        except Exception as e:
            log(f"⚠️ Errore copia icona {name}: {e}")

    return idle, processing


# ── MENU BAR APP ─────────────────────────────────
class CallTranscriberApp(rumps.App):
    def __init__(self, ffmpeg_bin: str, whisper_bin: str, model: Path,
                 icon_idle: Path, icon_processing: Path):
        self.ffmpeg_bin = ffmpeg_bin
        self.whisper_bin = whisper_bin
        self.model = model
        self.watch_folder = os.path.expanduser("~/CallRecordings")
        self.compress_video = True
        self.delete_original = False
        self.observer: Observer | None = None
        self._monitoring = False
        self._icon_idle = str(icon_idle)
        self._icon_processing = str(icon_processing)
        self._processing_flag = False

        super().__init__(
            APP_NAME,
            title="",   # nessun testo nella menu bar, solo icona
            icon=self._icon_idle,
            quit_button=None,
        )

        from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
        NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)

        self.folder_item = rumps.MenuItem("Cartella: …")
        self.toggle_item = rumps.MenuItem("Avvia monitoraggio")
        self.status_item = rumps.MenuItem("Stato: fermo")
        self.compress_item = rumps.MenuItem("Comprimi video")
        self.delete_item = rumps.MenuItem("Elimina originale")
        self.folder_item.set_callback(self.choose_folder)
        self.toggle_item.set_callback(self.toggle_monitoring)
        self.compress_item.set_callback(self.toggle_compress)
        self.delete_item.set_callback(self.toggle_delete)

        self.menu = [
            self.folder_item,
            None,
            self.toggle_item,
            self.status_item,
            None,
            rumps.MenuItem("Ultimi log", callback=self.show_logs),
            None,
            "Opzioni",
            self.compress_item,
            self.delete_item,
            None,
            rumps.MenuItem("Esci", callback=self.quit_app),
        ]
        self._update_folder_display()
        self._update_option_titles()

    @rumps.timer(1)
    def _sync_icon(self, _):
        """Timer: aggiorna l'icona sul main thread in base allo stato."""
        if self._processing_flag and self.icon != self._icon_processing:
            self.icon = self._icon_processing
        elif not self._processing_flag and self.icon != self._icon_idle:
            self.icon = self._icon_idle

    def notify(self, title: str, subtitle: str = "", message: str = ""):
        try:
            rumps.notification(title=title, subtitle=subtitle, message=message)
        except Exception:
            pass

    def _output_dir(self) -> Path:
        p = Path(self.watch_folder) / "output"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _update_folder_display(self):
        self.folder_item.title = f"Cartella: {self.watch_folder}"

    def _update_option_titles(self):
        self.compress_item.title = "Comprimi video  ✓" if self.compress_video else "Comprimi video"
        self.delete_item.title = "Elimina originale  ✓" if self.delete_original else "Elimina originale"

    def choose_folder(self, _):
        from AppKit import NSOpenPanel
        panel = NSOpenPanel.openPanel()
        panel.setCanChooseDirectories_(True)
        panel.setCanChooseFiles_(False)
        panel.setCanCreateDirectories_(True)
        panel.setMessage_("Scegli la cartella per le registrazioni")
        if panel.runModal() == 1:
            self.watch_folder = panel.URL().path()
            self._update_folder_display()
            if self._monitoring:
                self.stop_monitoring()
                self.start_monitoring()

    def toggle_compress(self, sender):
        self.compress_video = not self.compress_video
        self._update_option_titles()

    def toggle_delete(self, sender):
        self.delete_original = not self.delete_original
        self._update_option_titles()

    def toggle_monitoring(self, _):
        if self._monitoring:
            self.stop_monitoring()
        else:
            self.start_monitoring()

    def start_monitoring(self):
        log(f"🔍 Monitoraggio: {self.watch_folder}")
        output_dir = self._output_dir()

        def on_start():
            self._processing_flag = True

        def on_done(name: str, success: bool):
            self._processing_flag = False
            if success:
                self.notify("Trascrizione completata", name)
            else:
                self.notify("Errore", name, "Controlla i log per dettagli")

        Thread(target=queue_worker, args=(
            self.ffmpeg_bin, self.whisper_bin, self.model,
            output_dir, self.compress_video, self.delete_original,
            on_start, on_done
        ), daemon=True).start()
        self.observer = Observer()
        self.observer.schedule(VideoHandler(), self.watch_folder, recursive=False)
        self.observer.start()
        self._monitoring = True
        self.toggle_item.title = "Ferma monitoraggio"
        self.status_item.title = "Stato: attivo"

    def stop_monitoring(self):
        if self.observer:
            self.observer.stop()
            self.observer.join(timeout=5)
            self.observer = None
        self._monitoring = False
        self.toggle_item.title = "Avvia monitoraggio"
        self.status_item.title = "Stato: fermo"
        log("⏸️ Monitoraggio fermato")

    def show_logs(self, _):
        rumps.alert(
            title="Log (ultimi 20)",
            message="\n".join(LOG_LINES[-20:]) or "Nessun log.",
        )

    def quit_app(self, _):
        self.stop_monitoring()
        rumps.quit_application()


# ── MAIN ─────────────────────────────────────────
def main():
    # Log su file per debug bundle PyInstaller
    log_dir = Path.home() / ".calltranscriber"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "debug.log"

    def debug_log(msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        with open(log_path, "a") as f:
            f.write(f"[{ts}] {msg}\n")
        print(msg)

    try:
        debug_log("Avvio...")

        # Verifica dipendenze
        ffp = find_binary(["ffmpeg"])
        whp = find_binary(["whisper-cpp", "whisper-cli", "whisper"])
        if not ffp:
            debug_log("❌ ffmpeg non trovato")
            rumps.alert("Errore", "ffmpeg non trovato.\nInstalla: brew install ffmpeg")
            sys.exit(1)
        if not whp:
            debug_log("❌ whisper non trovato")
            rumps.alert("Errore", "whisper-cpp non trovato.\nInstalla: brew install whisper-cpp")
            sys.exit(1)
        debug_log(f"ffmpeg: {ffp}")
        debug_log(f"whisper: {whp}")

        # Icone
        debug_log("Carico icone...")
        icon_idle, icon_processing = ensure_icons()
        debug_log(f"Icone: {icon_idle}, {icon_processing}")

        # Scarica modello
        debug_log("Verifico modello...")
        model = ensure_model()
        if not model:
            debug_log("❌ modello non disponibile")
            rumps.alert("Errore", "Impossibile scaricare il modello whisper.")
            sys.exit(1)
        debug_log(f"Modello: {model}")

        # Avvia app
        debug_log("Avvio rumps...")
        app = CallTranscriberApp(ffmpeg_bin=ffp, whisper_bin=whp, model=model,
                                icon_idle=icon_idle, icon_processing=icon_processing)
        debug_log("run()...")
        app.run()
    except Exception as e:
        debug_log(f"💥 CRASH: {e}")
        import traceback
        debug_log(traceback.format_exc())
        raise


if __name__ == "__main__":
    main()
