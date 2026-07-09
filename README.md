# 🎙️ CallTranscriber

**macOS menu bar app — auto-transcribe video calls.**

Drop a video file → extract audio → transcribe with whisper-cpp → done.

- 🔒 100% on-device (no API keys, no cloud)
- 🇮🇹 Italian optimized (`large-v3-turbo`)
- ⚡ Apple Silicon native (whisper-cpp + Neural Engine)
- 🎬 Optional video compression (HEVC hardware encoder)

## Install

```bash
# Dependencies
brew install ffmpeg whisper-cpp

# Clone & build
git clone https://github.com/anhonestboy/calltranscriber.git
cd calltranscriber
pip3 install -r requirements.txt

# Run directly (dev)
python3 calltranscriber.py

# Or build standalone .app
pip3 install pyinstaller
./build.sh
# → dist/CallTranscriber.app

# Install
cp -r dist/CallTranscriber.app /Applications/
xattr -dr com.apple.quarantine /Applications/CallTranscriber.app  # Gatekeeper
```

First run downloads the whisper model (~1.5 GB) to `~/.calltranscriber/models/`.

## Usage

1. Click 🎙️ in the menu bar
2. Choose the folder where you save recordings
3. Click **Start monitoring**
4. Drop a `.mp4` / `.mov` / `.mkv` file
5. Output goes to `folder/output/`:
   - `name_audio.wav` — extracted audio
   - `name_trascrizione.txt` — Italian transcript
   - `name_compressed.mp4` — compressed video (optional)

## License

MIT
