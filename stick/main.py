import json, time, machine
from hw import Display, AXP, Buttons, Alert, H, rgb
from net import wifi, Fetch
from ui import UI, Menu, Detail, COL, READ, MARK, FG, DIM


class App:
    sessions, acked, ok, alerted, first = [], {}, False, set(), True


def home_items(app):
    unread = {s["id"] for s in pending(app)}
    rows = [("%s %s" % (MARK[s["state"]], s["name"]), COL[s["state"]] if s["id"] in unread or s["state"] == "working" else READ[s["state"]], lambda ui, s=s: ui.push(Detail(s["id"])), s["id"] in unread) for s in app.sessions]
    return (rows or [("no sessions", DIM, None)]) + [("Settings", FG, lambda ui: ui.push(settings))]


def settings_items(app):
    def toggle(ui):
        app.buzzer = not app.buzzer
        save()

    def cycle(ui):
        app.bright = (app.bright + 1) % 3
        axp.brightness(app.bright)
        save()
    return [("Light: " + ("low", "med", "high")[app.bright], FG, cycle),
            ("Buzzer: " + ("on" if app.buzzer else "off"), FG, toggle),
            ("WiFi " + ("ok" if wifi().isconnected() else "down"), DIM, None),
            ("Bridge " + ("ok" if app.ok else "down"), DIM, None),
            ("Reboot", FG, lambda ui: machine.reset())]


def pending(app):
    return [s for s in app.sessions if s["state"] != "working" and app.acked.get(s["id"]) != s["seq"]]


def ack_all(ui):
    for s in pending(ui.app):
        ui.app.acked[s["id"]] = s["seq"]


d, axp = Display(), AXP()
d.rect(0, 0, 240, H, 0)
d.text("Pager starting...", 8, 8, FG)
try:
    cfg = json.load(open("config.json"))
except (OSError, ValueError):
    d.text("Copy config.example.json", 8, 30, FG)
    d.text("to config.json, fill it", 8, 42, FG)
    d.text("in, and reboot.", 8, 54, FG)
    while True:  # not SystemExit: that soft-resets and re-runs main.py in a loop
        time.sleep(1)

app = App()
try:
    prefs = json.load(open("prefs.json"))
except (OSError, ValueError):
    prefs = {}
app.axp, app.bright, app.buzzer = axp, prefs.get("bright", 1), prefs.get("buzzer", cfg.get("buzzer", True))
axp.brightness(app.bright)


def save():
    with open("prefs.json", "w") as f:
        json.dump({"bright": app.bright, "buzzer": app.buzzer}, f)


btn, alert = Buttons(axp), Alert()


poller, poster, fails = Fetch(), Fetch(), 3


def answer(sid, v):
    try:
        poster.start(cfg["bridge"], cfg["port"], cfg["token"], "POST", "/answer", json.dumps({"id": sid, "v": v}).encode())
    except OSError:
        pass  # the Mac's own dialog is still there as the fallback


app.answer = answer
w = wifi()
w.active(True)
settings = Menu("Settings", settings_items)
ui = UI(d, app, Menu(lambda a: "PAGER (%d new)" % len(pending(a)) if pending(a) else "PAGER", home_items, ack_all))
last_poll, last_input, last_conn = -10000, time.ticks_ms(), -10000

while True:
    now = time.ticks_ms()
    if not poller.s and time.ticks_diff(now, last_poll) > 2000:
        last_poll = now
        if not w.isconnected():
            fails = 3
            if time.ticks_diff(now, last_conn) > 10000:  # connect() is async; retry sparingly
                last_conn = now
                w.connect(cfg["ssid"], cfg["password"])
        else:
            try:
                poller.start(cfg["bridge"], cfg["port"], cfg["token"], "GET", "/state")
            except OSError:
                fails += 1
        ui.dirty = True
    if poller.s:  # the network never blocks the buttons: poll it a bit each tick
        try:
            r = poller.step()
            if r:
                if r[0] != 200:
                    raise OSError(r[0])
                app.sessions, fails = json.loads(r[1]), 0
                ui.dirty = True
        except (OSError, ValueError):
            fails += 1
            ui.dirty = True
    if poster.s:
        try:
            poster.step()
        except OSError:
            pass
    app.ok = fails < 3  # a few lost polls are not a lost link
    if app.ok and app.first:  # whatever is already waiting at boot is not news
        app.first = False
        app.acked = {s["id"]: s["seq"] for s in app.sessions}

    pend = pending(app)
    new = {(s["id"], s["seq"]) for s in pend} - app.alerted
    if new:
        app.alerted |= new
        last_input = now
        axp.backlight(True)
        if app.buzzer:
            alert.beep(2 if any(s["state"] == "waiting" for s in pend) else 1)
    alert.blink(None if not pend else "urgent" if any(s["state"] == "waiting" for s in pend) else "gentle")

    k = btn.poll()
    if k:
        last_input = now
        if axp.bl:
            ui.key(k)
        else:
            axp.backlight(True)  # first press only wakes the screen
    elif axp.bl and not pend and time.ticks_diff(now, last_input) > 30000:
        axp.backlight(False)

    if ui.dirty:
        ui.draw()
    time.sleep_ms(20)
