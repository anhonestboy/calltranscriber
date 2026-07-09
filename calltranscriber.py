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
    for name in names:
        # brew --prefix name
        r = subprocess.run(["brew", "--prefix", name], capture_output=True, text=True)
        if r.returncode == 0:
            prefix = r.stdout.strip()
            for variant in [name, name.replace("-cpp", ""), name.replace("-cli", ""), "whisper-cli"]:
                candidate = os.path.join(prefix, "bin", variant)
                if os.path.isfile(candidate):
                    return candidate
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
        "-c:v", "hevc_videotoolbox", "-b:v", "2M",
        "-c:a", "aac", "-b:a", "64k",
        "-movflags", "+faststart",
        str(dst)
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode == 0


def process_video(video: Path, output_dir: Path, ffmpeg_bin: str, whisper_bin: str,
                  model: Path, compress: bool, delete_original: bool):
    global PROCESSING
    base = video.stem
    audio_path = output_dir / f"{base}_audio.wav"
    text_path = output_dir / f"{base}_trascrizione.txt"
    compressed_path = output_dir / f"{base}_compressed.mp4"

    if not extract_audio(video, audio_path, ffmpeg_bin):
        PROCESSING = False
        return
    log(f"   → {audio_path.name}")

    if not transcribe(audio_path, text_path, whisper_bin, model):
        PROCESSING = False
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


def queue_worker(ffmpeg_bin: str, whisper_bin: str, model: Path,
                 output_dir: Path, compress: bool, delete_original: bool):
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
                            model, compress, delete_original)
            except Exception as e:
                log(f"💥 Errore: {e}")
                PROCESSING = False
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


# ── ICON GENERATION ──────────────────────────────
def ensure_icon() -> Path:
    """Genera un'icona PNG per la menu bar se non esiste."""
    icon_dir = MODEL_DIR.parent  # ~/.calltranscriber/
    icon_path = icon_dir / "icon.png"
    if icon_path.exists():
        return icon_path

    try:
        from PIL import Image, ImageDraw
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        # Cerchio blu scuro
        draw.ellipse([4, 4, 60, 60], fill=(30, 64, 175, 255))
        # Microfono bianco stilizzato
        draw.rectangle([26, 12, 38, 36], fill=(255, 255, 255, 255))
        draw.pieslice([22, 8, 42, 20], 180, 360, fill=(255, 255, 255, 255))
        draw.rectangle([26, 36, 30, 48], fill=(255, 255, 255, 255))
        draw.arc([14, 42, 50, 58], 60, 300, fill=(255, 255, 255, 255), width=4)
        img.save(icon_path, "PNG")
        log("🎨 Icona generata")
    except ImportError:
        # Pillow non installato — crea una PNG minimale (pixel azzurro 1x1)
        b = bytes([137, 80, 78, 71, 13, 10, 26, 10, 0, 0, 0, 13, 73, 72, 68, 82,
                   0, 0, 0, 1, 0, 0, 0, 1, 8, 2, 0, 0, 0, 144, 119, 83, 222,
                   0, 0, 0, 12, 73, 68, 65, 84, 8, 215, 99, 96, 96, 96, 0, 0,
                   0, 4, 0, 1, 39, 221, 37, 58, 0, 0, 0, 0, 73, 69, 78, 68,
                   174, 66, 96, 130])
        icon_path.write_bytes(b)
    return icon_path


# ── MENU BAR APP ─────────────────────────────────
class CallTranscriberApp(rumps.App):
    def __init__(self, ffmpeg_bin: str, whisper_bin: str, model: Path, icon: Path):
        self.ffmpeg_bin = ffmpeg_bin
        self.whisper_bin = whisper_bin
        self.model = model
        self.watch_folder = os.path.expanduser("~/CallRecordings")
        self.compress_video = True
        self.delete_original = False
        self.observer: Observer | None = None
        self._monitoring = False
        self.icon_path = icon

        super().__init__(
            APP_NAME,
            title="",   # nessun testo nella menu bar, solo icona
            icon=str(icon),
            quit_button=None,
        )

        from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
        NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)

        self.folder_item = rumps.MenuItem("📂 Cartella: …")
        self.toggle_item = rumps.MenuItem("Avvia monitoraggio")
        self.status_item = rumps.MenuItem("Stato: ⏸️ fermo")
        self.compress_item = rumps.MenuItem("✅ Comprimi video")
        self.delete_item = rumps.MenuItem("☐ Elimina originale")
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
            rumps.MenuItem("📋 Ultimi log", callback=self.show_logs),
            None,
            "⚙️ Opzioni",
            self.compress_item,
            self.delete_item,
            None,
            rumps.MenuItem("Esci", callback=self.quit_app),
        ]
        self._update_folder_display()
        self._update_option_titles()

    def _output_dir(self) -> Path:
        p = Path(self.watch_folder) / "output"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _update_folder_display(self):
        self.folder_item.title = f"📂 {self.watch_folder}"

    def _update_option_titles(self):
        self.compress_item.title = "✅ Comprimi video" if self.compress_video else "☐ Comprimi video"
        self.delete_item.title = "✅ Elimina originale" if self.delete_original else "☐ Elimina originale"

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
        Thread(target=queue_worker, args=(
            self.ffmpeg_bin, self.whisper_bin, self.model,
            output_dir, self.compress_video, self.delete_original
        ), daemon=True).start()
        self.observer = Observer()
        self.observer.schedule(VideoHandler(), self.watch_folder, recursive=False)
        self.observer.start()
        self._monitoring = True
        self.toggle_item.title = "Ferma monitoraggio"
        self.status_item.title = "Stato: ▶️ attivo"

    def stop_monitoring(self):
        if self.observer:
            self.observer.stop()
            self.observer.join(timeout=5)
            self.observer = None
        self._monitoring = False
        self.toggle_item.title = "Avvia monitoraggio"
        self.status_item.title = "Stato: ⏸️ fermo"
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

        # Icona
        debug_log("Creo icona...")
        icon = ensure_icon()
        debug_log(f"Icona: {icon}")

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
        app = CallTranscriberApp(ffmpeg_bin=ffp, whisper_bin=whp, model=model, icon=icon)
        debug_log("run()...")
        app.run()
    except Exception as e:
        debug_log(f"💥 CRASH: {e}")
        import traceback
        debug_log(traceback.format_exc())
        raise


if __name__ == "__main__":
    main()
