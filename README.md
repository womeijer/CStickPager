# CStickPager

A pocket pager for [Claude Code](https://claude.com/claude-code), built on an **M5StickC Plus** running MicroPython.

It shows which of your Claude Code sessions are working, waiting for you, or done. It beeps and blinks when one needs you, shows Claude's last message, and lets you **Allow or Deny permission prompts from the stick**.

```
Claude Code ──hooks──▶ bridge (Mac, Python) ◀──WiFi──▶ stick (MicroPython)
```

- **Hooks** (a `curl` in your Claude Code settings) send session events to the bridge.
- **Bridge** (`bridge/bridge.py`, stdlib only) keeps the session table and serves it to the stick.
- **Stick** (`stick/`) polls the bridge over WiFi, draws the UI, and sends your Allow/Deny back.

It works with the Claude desktop app's Code tab and the terminal CLI, since both run Claude Code hooks.

## What it does

| Feature | Notes |
|---|---|
| Session list | Colour by state: blue working, red waiting (needs you), green done. Rows you haven't opened yet are bright and have a dot; opened ones are dim. |
| Alerts | Red LED blinks fast (waiting) or pulses slowly (done); buzzer beeps twice or once. The screen wakes. |
| Last message | Open a session to read Claude's last reply, word-wrapped, A pages through it. |
| Allow / Deny | When Claude asks permission to run a tool, the stick shows the command and you choose. If you don't answer in 20 s, or the stick is off, the normal dialog appears on your Mac. |
| Settings | Backlight (3 steps, never fully off), buzzer on/off (both saved on the stick), reboot. |
| Battery | 4-bar icon in the header (rough, reads high while charging). |

### Controls

| Button | List | Reading a message | Permission prompt |
|---|---|---|---|
| **B** (side) | next row | back | switch Allow / Deny |
| **A** (front) | open row | next page (wraps) | confirm |
| **Power** (click) | mark all read | back | leave it to the Mac's dialog |

The screen sleeps after 30 s idle (unless an alert is pending). The first press only wakes it.

## What you need

- **M5StickC Plus** (the original, with the AXP192 power chip). The **Plus2 is not supported**: different power circuitry and pins.
- A **2.4 GHz** WiFi network (the ESP32 has no 5 GHz). The Mac and the stick must be on the same network.
- A Mac (Linux should work, but only macOS was tested) with Python 3.9+ and Claude Code.
- A USB-C data cable.

## Setup

### 1. Get the tools

```bash
git clone <this repo> && cd CStickPager
python3 -m venv .venv
.venv/bin/pip install esptool mpremote
```

### 2. Flash MicroPython onto the stick

Stock MicroPython, the generic ESP32 build (tested with v1.29.0). Download the latest `.bin` from <https://micropython.org/download/ESP32_GENERIC/> into `fw/`:

```bash
mkdir -p fw && curl -o fw/mp.bin https://micropython.org/resources/firmware/ESP32_GENERIC-20260824-v1.29.0.bin
```

Plug the stick in and find its port (it appears as `/dev/cu.usbserial-*`, or `/dev/ttyUSB0` on Linux; some units use a CH9102 chip and may need its driver):

```bash
ls /dev/cu.usbserial*
export PAGER_PORT=/dev/cu.usbserial-XXXX     # your port
```

Erase, then write the firmware at `0x1000`:

```bash
.venv/bin/esptool --port $PAGER_PORT erase-flash
.venv/bin/esptool --port $PAGER_PORT --baud 115200 write-flash 0x1000 fw/mp.bin
```

Use 115200 baud. Faster speeds failed right after an erase on our units. If the connection fails, unplug and replug the stick and retry. When it prints `Hash of data verified`, the flash is done.

### 3. Configure the stick

```bash
cp stick/config.example.json stick/config.json
```

Edit `stick/config.json`:

```json
{"ssid": "YourWiFi", "password": "...", "bridge": "192.168.1.20", "port": 8787, "token": "a-long-random-string", "buzzer": true}
```

- `bridge` is your **Mac's LAN IP** (`ipconfig getifaddr en0`, or `en1` on some Macs). Giving the Mac a fixed address (a DHCP reservation in your router) stops this from breaking later.
- `token` is a shared secret. Generate one: `python3 -c "import secrets; print(secrets.token_hex(8))"`. **Set it**: without a token the bridge refuses all stick requests, so the stick can't connect and nobody on your network can read your sessions or approve tools.

`config.json` is git-ignored because it holds your WiFi password.

### 4. Copy the app to the stick

```bash
./deploy.sh
```

This finds the serial port itself (or uses `PAGER_PORT`), copies the files and reboots the stick. You'll see "PAGER" with `NOLINK` until the bridge is running.

### 5. Start the bridge

```bash
python3 bridge/bridge.py
```

It listens on port 8787 and reads the token from `stick/config.json`. Within a few seconds the stick drops `NOLINK`. Keep it running; it stops when you log out or reboot (a launchd/systemd unit is a good next step).

Optional environment variables for the bridge: `PAGER_HTTP_PORT` (default 8787; keep `port` in `config.json` in step), `PAGER_TOKEN` (overrides the config file) and `PAGER_ASK_WAIT` (seconds the stick has to answer a permission prompt, default 20). `PAGER_PORT` is only for `deploy.sh`, where it names the *serial* port.

### 6. Add the hooks to Claude Code

Merge the `hooks` from [`hooks/settings-snippet.json`](hooks/settings-snippet.json) into `~/.claude/settings.json`. If you have no hooks yet, you can copy the block as is; if you do, add each event's entry to your existing list.

The hooks run `curl` against `http://localhost:8787` and always succeed (`|| true`), so Claude is unaffected when the bridge is down. They apply to **sessions started after** the change.

Test it: start a Claude session, send a prompt, and watch the project name appear on the stick.

## Testing without Claude

Send fake events (run from the project folder; the token is the one in `config.json`):

```bash
curl -s --data-binary '{"hook_event_name":"Stop","session_id":"t","cwd":"/x/demo","transcript_path":""}' localhost:8787/hook
```

Fake a permission prompt (waits up to 20 s for you to answer on the stick, then prints the hook's reply):

```bash
curl -s -m 25 --data-binary '{"session_id":"t1","cwd":"/x/test","tool_name":"Bash","tool_input":{"command":"rm -rf node_modules"}}' localhost:8787/ask
```

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `NOLINK` in the header / "Bridge down" in Settings | Bridge isn't running, the Mac's IP changed (update `bridge` in `config.json`, run `./deploy.sh`), the Mac is asleep, or the token doesn't match. It shows after 3 failed polls and clears on its own. |
| Screen black | It's the 30 s idle sleep: press any button. Settings > Light might also be on a low step. |
| Screen shows scattered pixels | Display panels vary between batches. The driver sends M5Stack's full ST7789 init sequence for this reason; if your unit still misbehaves, lower `baudrate` in `stick/hw.py`. |
| Stick freezes for a second | Your Mac's WiFi has latency spikes (AirDrop/AWDL or a crowded channel are common causes); wired Ethernet for the Mac helps. The stick tolerates stalls but can't remove them. |
| Nothing appears when you use Claude | Hooks not merged, or session started before you added them. Check `curl -s -H "X-Token: <token>" localhost:8787/state`. |
| Permission prompt doesn't reach the stick | The stick must have polled in the last 6 s, and Claude must be in a mode that asks (not auto-accept) for a command that isn't already allowed. |
| Flash fails | Lower the baud rate, replug the cable, make sure it's a data cable. |

## Security notes

- The bridge listens on all interfaces. `/state` and `/answer` need the shared token (**which means anyone with the token can approve tools on your Mac**); `/hook` and `/ask` only accept connections from `localhost`.
- The token and WiFi password are stored in plain text on the stick and in `stick/config.json`.
- The PermissionRequest hook only ever turns a prompt into an Allow/Deny you chose; it falls back to Claude's normal dialog on timeout, an error, or when the stick is off.
- Treat the repo as a hobby project: it has no TLS and was not security-reviewed.

## Not done yet

- **Replies like "continue" / "stop".** Nothing lets an outside program type into a running Claude desktop session. Possible routes are channels (needs a launch flag, desktop support unconfirmed) or a terminal multiplexer for CLI sessions.
- **Starting the bridge at login.**
- Screen-sleep timeout setting.
- Allow/Deny has been tested end to end with simulated prompts; real Claude prompts through the hook are still worth a hands-on check.

## Repo layout

```
bridge/bridge.py        HTTP server: session table, /hook /ask /answer /state
hooks/settings-snippet.json   Claude Code hooks to merge into ~/.claude/settings.json
stick/                  MicroPython app (copied to the board by deploy.sh)
  main.py               event loop, polling, alerts
  hw.py                 display (ST7789), power chip (AXP192), buttons, LED, buzzer
  net.py                non-blocking HTTP client
  ui.py                 screen stack and screens
  config.example.json   copy to config.json and edit
deploy.sh               copy stick/ to the board and reboot it
DESIGN.md               design notes and decisions
```

The UI is a stack of screens, each with `draw`, `next`, `select` and `back`. To add one, write the class and `ui.push()` it from a menu entry.
