#!/bin/sh
# Copies stick/*.py (and config.json if present) to the board and resets it.
# Serial port: $PAGER_PORT, else the first USB serial device found.
cd "$(dirname "$0")/stick" || exit 1
PORT=$PAGER_PORT
for p in /dev/cu.usbserial* /dev/ttyUSB* /dev/cu.usbmodem* /dev/ttyACM*; do
  [ -z "$PORT" ] && [ -e "$p" ] && PORT=$p
done
[ -n "$PORT" ] || { echo "no serial port found: plug in the stick or set PAGER_PORT" >&2; exit 1; }
M="${MPREMOTE:-../.venv/bin/mpremote} connect $PORT"
echo "using $PORT"
$M fs cp hw.py net.py ui.py main.py : && { [ -f config.json ] && $M fs cp config.json :; $M reset; }
