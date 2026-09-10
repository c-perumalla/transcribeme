#!/usr/bin/env bash
# Tests audio capture directly with ffmpeg, bypassing the app, so macOS
# permission problems and device quirks show their real error message.
set -uo pipefail
cd "$(dirname "$0")"

DEV_INDEX="${1:-0}"
OUT="$(mktemp -d)/probe.wav"

echo "=== Which app owns the microphone grant? ==="
echo "The permission belongs to the app that launched THIS shell:"
ps -o comm= -p "$PPID" 2>/dev/null | sed 's/^/  parent: /'
echo "  (If that is VS Code / Cursor / iTerm, THAT app needs the grant —"
echo "   not Terminal.app. Quit and reopen it fully after granting.)"
echo

echo "=== Audio devices ffmpeg can see ==="
ffmpeg -hide_banner -f avfoundation -list_devices true -i "" 2>&1 \
  | sed -n '/audio devices/,$p' | sed 's/\[AVFoundation indev @ [^]]*\] //'
echo

echo "=== Capturing 3s from audio device :$DEV_INDEX ==="
ffmpeg -hide_banner -loglevel info -f avfoundation -i ":$DEV_INDEX" \
       -flush_packets 1 -t 3 -ar 16000 -ac 1 -c:a pcm_s16le "$OUT" 2>&1 \
  | grep -vE "^\s*(configuration|lib[a-z]+ )" | tail -20
echo

if [[ -f "$OUT" ]]; then
  SIZE=$(stat -f%z "$OUT" 2>/dev/null || echo 0)
  echo "=== Result ==="
  echo "  file: $OUT"
  echo "  bytes: $SIZE (a 3s 16kHz mono capture should be ~96000)"
  if (( SIZE > 44 )); then
    echo "  Peak level:"
    ffmpeg -v error -i "$OUT" -af volumedetect -f null - 2>&1 \
      | grep -E "max_volume|mean_volume" | sed 's/^/    /'
    echo
    echo "  CAPTURE WORKS. If max_volume is -91 dB the device is live but"
    echo "  silent (muted, or wrong input) — that is not a permission problem."
  else
    echo "  NO AUDIO CAPTURED — header only."
    echo "  ffmpeg opened the device but got no frames. That is the signature"
    echo "  of a missing microphone grant for the parent app named above."
  fi
else
  echo "=== Result: ffmpeg wrote no file at all (see its error above) ==="
fi
