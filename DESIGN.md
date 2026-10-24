# CStickPager: design (draft 2)

A pocket pager for Claude Code. The M5StickC shows which sessions are working, waiting or done, alerts you when one needs you, and sends a canned reply (continue / stop / yes / no) back to it.

## Hardware assumptions (confirm in Open Questions)

| Part | M5StickC | M5StickC Plus |
|---|---|---|
| Screen | 80x160 ST7735 | 135x240 ST7789 |
| Buttons | A (front), B (side) | same |
| Third button | Power button (AXP192): click only, no hold | same |
| LED | Red, GPIO10, active low | same |
| Buzzer | none | GPIO2 |
| Radio | WiFi 2.4 GHz only | same |

Three buttons: **B = next**, **A = select**, **Power click = back**. Reading the power button means polling the AXP192 over I2C (IRQ status register), which is a few lines of MicroPython.

## Architecture

```
Claude Code ──hooks──▶ bridge (Mac, Python) ◀──WiFi──▶ Stick (MicroPython)
      ▲                      │
      └──── inject reply ────┘
```

Three pieces:

1. **Hooks (Mac).** A tiny `curl` command in Claude Code's `settings.json` for `UserPromptSubmit`, `Notification`, `Stop` and `SessionEnd`. Each POSTs `{session_id, cwd, event, message}` to the bridge. This is how the pager learns status, and it needs no changes to Claude itself.
2. **Bridge (Mac).** A single-file Python server (stdlib only). It keeps a table of sessions and their state, and serves the stick. It is also the only place that knows how to deliver a reply.
3. **Stick (MicroPython).** Connects to WiFi, polls the bridge, draws the UI, blinks the LED, and POSTs a reply when you pick one.

The stick talks plain HTTP to the bridge: `GET /state` (long-poll with a version number so alerts arrive quickly) and `POST /reply`. HTTP is easy to debug with `curl` and needs no extra MicroPython libraries. MQTT or WebSocket would be lower latency, but adds a broker or library for nothing at this scale.

## Session states

| State | Set by | Meaning | Alert? |
|---|---|---|---|
| working | `UserPromptSubmit` | Claude is busy | no |
| waiting | `Notification` | needs permission or input | **yes**, urgent (fast blink + beep) |
| done | `Stop` | finished a turn | yes, gentle (slow pulse, one beep) |
| gone | `SessionEnd` | closed | no, removed after a timeout |

A session is labelled by its folder name (`basename(cwd)`). Alerts clear when you open that session on the stick or when it goes back to working.

## The hard part: delivering a reply

Hooks can tell the pager what is happening, but nothing in Claude Code lets an outside process type into a running session. Options, from most to least robust:

- **A. tmux.** Run Claude Code inside tmux; the bridge calls `tmux send-keys -t <pane> "continue" Enter`. The hook records `$TMUX_PANE` so each session maps to its pane. Reliable, and works for "yes/no" on permission prompts too (send `1` / `Esc`). Only works for terminal sessions started in tmux.
- **B. AppleScript / keystroke injection** into the front terminal window. Works with any terminal, but is fragile: it types into whatever has focus.
- **C. Hook-based replies.** For permission requests, a `PermissionRequest`/`PreToolUse` hook could *block waiting for the stick*, then return allow or deny. That gives yes/no with no keystroke injection, but only for permission prompts, and the terminal stays blocked until you answer. "Continue" and "stop" for free text still need A or B.
- **D. Claude Code Remote Control / Desktop app.** Not covered by hooks or tmux; I would need to research what API exists.

**Proposal:** build status and alerts first (hooks → bridge → stick), then add replies via **A (tmux)** for everything, optionally **C** for yes/no later.

## Stick UI

A small menu stack, so adding a screen is adding one class or dict entry, not rewriting the loop.

```
Home (session list)          B: next session    A: open     Power: (none)
 └─ Session (status + last message)
     └─ Reply menu           B: next reply      A: send     Power: back
         continue / stop / yes / no / (extensible)
Settings                     (reached from the end of the Home list)
 └─ WiFi: scan, pick, enter password
```

Each screen is an object with `draw()`, `on_next()`, `on_select()`, `on_back()`. The stock replies and settings entries are plain lists, which is the extension point. Any new screen is another such object pushed onto the stack.

**WiFi setup** is awkward with three buttons. Proposal: credentials live in `config.json` on the device, and the on-device WiFi screen only picks from saved networks. First-time setup is by editing the file over USB (`mpremote`). A captive-portal setup mode is possible later.

## Alerts

- LED (GPIO10, active low): urgent = fast blink, gentle = slow pulse. Blinking continues until you open the session, then stops.
- Buzzer: Plus only. Setting `buzzer = true/false` in config. On the original StickC it is skipped silently.
- Screen wakes on alert and dims after idle, to save battery (about 80 mAh on the original).

## Files

```
bridge/bridge.py      HTTP server, session table, reply delivery
hooks/notify.sh       one-liner called by Claude Code hooks
stick/main.py         event loop
stick/ui.py           screen stack + screens
stick/net.py          wifi + bridge client
stick/config.json     wifi, bridge address, buzzer flag
```

## Milestones

1. MicroPython firmware flashed; hello world + button test (A, B, power).
2. Bridge + hooks; `curl /state` shows live sessions.
3. Stick connects to WiFi and shows the session list.
4. Alerts (LED, buzzer).
5. Reply menu + tmux delivery.
6. Polish: sleep/dim, reconnect, config screen.

## Out of scope for now

Reading full transcripts on screen, voice, BLE, running the bridge off the Mac, auth beyond a shared token on the LAN.

## Open questions

See the chat message that accompanies this document.

## Status (2026-10-23)

Decisions: M5StickC **Plus**; Claude **desktop app**, so replies are out of scope for now; bridge on the Mac only; WiFi set by editing `stick/config.json`; stock MicroPython 1.29.

Done and verified on hardware: milestones 1-4 (WiFi, session list, LED + buzzer alerts, three-button navigation) and real hooks from the desktop app.

Not done: replies (continue / stop / yes / no). Needs a way to send text into the desktop app; nothing researched yet. Also not done: start-at-login for the bridge, battery-saving sleep beyond the 30 s backlight timeout.

Gotchas learned: `raise SystemExit` in `main.py` soft-resets and loops; a full-screen buffer (65 KB) does not fit in RAM, so draw by rows; `bytearray * int` is unsupported.
