#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

APP_NAME="CallTranscriber"
ICON_SCRIPT="scripts/generate_icon.py"
ICON_PATH="icon.png"

echo "🔨 CallTranscriber — PyInstaller build"
echo ""

# Check deps
if ! python3 -c "import PyInstaller" 2>/dev/null; then
    echo "❌ PyInstaller non trovato. Installa: pip3 install pyinstaller"
    exit 1
fi

# Generate icon if needed
if [ ! -f "$ICON_PATH" ]; then
    echo "🎨 Genero icona..."
    python3 "$ICON_SCRIPT" "$ICON_PATH"
fi

# Convert PNG to ICNS for macOS
if [ -f "$ICON_PATH" ] && [ ! -f "icon.icns" ]; then
    echo "🖼️  Converto icona in .icns..."
    mkdir -p icon.iconset
    sips -z 16 16   "$ICON_PATH" --out icon.iconset/icon_16x16.png
    sips -z 32 32   "$ICON_PATH" --out icon.iconset/icon_16x16@2x.png
    sips -z 32 32   "$ICON_PATH" --out icon.iconset/icon_32x32.png
    sips -z 64 64   "$ICON_PATH" --out icon.iconset/icon_32x32@2x.png
    sips -z 128 128 "$ICON_PATH" --out icon.iconset/icon_128x128.png
    sips -z 256 256 "$ICON_PATH" --out icon.iconset/icon_128x128@2x.png
    sips -z 256 256 "$ICON_PATH" --out icon.iconset/icon_256x256.png
    sips -z 512 512 "$ICON_PATH" --out icon.iconset/icon_256x256@2x.png
    sips -z 512 512 "$ICON_PATH" --out icon.iconset/icon_512x512.png
    iconutil -c icns icon.iconset -o icon.icns
    rm -rf icon.iconset
fi

# Clean
rm -rf build dist *.spec

echo "📦 PyInstaller build..."
pyinstaller \
    --windowed \
    --name "$APP_NAME" \
    --icon icon.icns \
    --add-data "icon.png:." \
    --add-data "icon_processing.png:." \
    --osx-bundle-identifier com.calltranscriber.app \
    --clean \
    calltranscriber.py

# Aggiungi LSUIElement (solo menu bar, niente dock)
echo "🔧 Imposto LSUIElement=true..."
/usr/libexec/PlistBuddy -c "Add :LSUIElement bool true" "dist/$APP_NAME.app/Contents/Info.plist" 2>/dev/null || \
/usr/libexec/PlistBuddy -c "Set :LSUIElement true" "dist/$APP_NAME.app/Contents/Info.plist"

# Fix permessi app bundle
chmod -R +r "dist/$APP_NAME.app"

echo ""
echo "✅ Build completata!"
echo "   App: dist/$APP_NAME.app"
echo ""
echo "Per installare: cp -r dist/$APP_NAME.app /Applications/"
echo ""
echo "Al primo avvio: tasto destro → Apri (Gatekeeper)"
echo "L'app scaricherà il modello whisper (~1.5 GB) al primo avvio."
