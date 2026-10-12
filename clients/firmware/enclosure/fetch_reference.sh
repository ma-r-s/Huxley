#!/usr/bin/env bash
# Download Waveshare's official mechanical + electrical references for the
# ESP32-S3-AUDIO-Board into reference/ (gitignored: vendor files, not ours to
# redistribute in a public repo).
#
#   STEP / DXF / PDF drawing  -> reference/drawing/ESP32-S3-AUDIO-Board.{stp,dxf,pdf}
#   Schematic v1.1            -> reference/ESP32-S3-AUDIO-Board_1.1.pdf
#   Outline + pinout images   -> reference/*.jpg
#
# Source pages: https://docs.waveshare.com/ESP32-S3-AUDIO-Board/Resources-And-Documents
#               https://www.waveshare.com/wiki/ESP32-S3-AUDIO-Board
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p reference
cd reference

base=https://files.waveshare.com/wiki/ESP32-S3-AUDIO-Board
curl -fsSL -o ESP32-S3-AUDIO-Board_1.1.pdf "$base/ESP32-S3-AUDIO-Board_1.1.pdf"
curl -fsSL -o ESP32-S3-AUDIO-Board.rar "$base/ESP32-S3-AUDIO-Board.rar"

img=https://www.waveshare.com/w/upload
curl -fsSL -o outline-dimensions.jpg "$img/4/45/ESP32-S3-AUDIO-Board-details-size.jpg"
curl -fsSL -o onboard-resources.jpg "$img/1/13/ESP32-S3-AUDIO-Board-details-intro.jpg"
curl -fsSL -o pinout.jpg "$img/7/73/Esp32s3_audio_boarddetails-inter-1.1.jpg"

# The .rar holds the STEP assembly, the DXF and a dimensioned PDF.
# `unar` ships with `brew install unar`.
rm -rf drawing
unar -q -o . ESP32-S3-AUDIO-Board.rar
ls -la drawing
