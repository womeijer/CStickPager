import gc, json, os, sys, time, machine
from hw import Display, AXP, Buttons, Alert, H, rgb
from net import wifi, Fetch
from ui import UI, Menu, Detail, COL, READ, MARK, FG, DIM


SLEEP = (("30s", 30000), ("2min", 120000), ("10min", 600000), ("never", None))  # screen sleep after this much idle time


class App:
    sessions, acked, ok, alerted, first = [], {}, False, set(), True
    nfail, err = 0, "-"  # link diagnostics shown in Settings


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
    def sleep(ui):
        app.sleep = (app.sleep + 1) % len(SLEEP)
        save()
    return [("Light: " + ("low", "med", "high")[app.bright], FG, cycle),
            ("Sleep: " + SLEEP[app.sleep][0], FG, sleep),
            ("Buzzer: " + ("on" if app.buzzer else "off"), FG, toggle),
            ("WiFi " + ("ok" if wifi().isconnected() else "down"), DIM, None),
            ("Bridge " + ("ok" if app.ok else "down"), DIM, None),
            ("%d %s" % (app.nfail, app.err), DIM, None),
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
app.sleep = prefs.get("sleep", 0) % len(SLEEP)
axp.brightness(app.bright)


def log(msg):
    """Link up/down changes only, so the file stays tiny; cleared when it grows past 3 KB."""
    try:
        w = wifi()
        line = "%ds %s | err=%s wifi=%s st=%s rssi=%s mem=%d\n" % (time.ticks_ms() // 1000, msg, app.err, w.isconnected(), w.status(), w.status("rssi") if w.isconnected() else "-", gc.mem_free())
        with open("link.log", "a") as f:
            f.write(line)
        if os.stat("link.log")[6] > 3000:
            os.remove("link.log")
    except OSError:
        pass


def save():
    with open("prefs.json", "w") as f:
        json.dump({"bright": app.bright, "buzzer": app.buzzer, "sleep": app.sleep}, f)


btn, alert = Buttons(axp), Alert()


poller, poster, fails = Fetch(keep=True), Fetch(), 3


outbox, last_ans = [], 0  # answers not yet confirmed by the bridge: [session id, verdict, failed tries]


def answer(sid, v):
    outbox.append([sid, v, 0])  # sent from the main loop, and retried until the bridge confirms


app.answer = answer
w = wifi()
w.active(True)
settings = Menu("Settings", settings_items)
ui = UI(d, app, Menu(lambda a: "PAGER (%d new)" % len(pending(a)) if pending(a) else "PAGER", home_items, ack_all))
last_poll, last_input, last_conn = -10000, time.ticks_ms(), -10000
down_at, heals = 0, 0
usb, last_usb, redraw_at, last_redraw = axp.r(0x00) & 0x20, 0, 0, time.ticks_ms()

try:
    while True:
        now = time.ticks_ms()
        if not poller.busy and time.ticks_diff(now, last_poll) > 2000:
            last_poll = now
            if not w.isconnected():
                fails, app.err = 3, "wifi down"
                if time.ticks_diff(now, last_conn) > 10000:  # connect() is async; retry sparingly
                    last_conn = now
                    w.connect(cfg["ssid"], cfg["password"])
            else:
                try:
                    poller.start(cfg["bridge"], cfg["port"], cfg["token"], "GET", "/state")
                except OSError as e:
                    fails, app.nfail, app.err = fails + 1, app.nfail + 1, str(e)[:11]
            ui.dirty = True
        if poller.busy:  # the network never blocks the buttons: poll it a bit each tick
            try:
                r = poller.step()
                if r:
                    if r[0] != 200:
                        raise OSError(r[0])
                    app.sessions, fails = json.loads(r[1]), 0
                    ui.dirty = True
            except (OSError, ValueError) as e:
                fails, app.nfail, app.err = fails + 1, app.nfail + 1, str(e)[:11]
                ui.dirty = True
        if poster.busy:
            try:
                if poster.step():
                    outbox.pop(0)  # confirmed (204), or the request is gone (404): either way, done
            except OSError as e:
                outbox[0][2] += 1
                app.err = "ans " + str(e)[:7]
                log("answer failed")
                if outbox[0][2] >= 10:
                    outbox.pop(0)
        elif outbox and time.ticks_diff(now, last_ans) > 400:
            last_ans = now
            try:
                poster.start(cfg["bridge"], cfg["port"], cfg["token"], "POST", "/answer", json.dumps({"id": outbox[0][0], "v": outbox[0][1]}).encode())
            except OSError as e:
                outbox[0][2] += 1
                app.err = "ans " + str(e)[:7]
                log("answer start failed")
                if outbox[0][2] >= 10:
                    outbox.pop(0)
        if (fails < 3) != app.ok:
            app.ok = fails < 3  # a few lost polls are not a lost link
            down_at, heals = now, 0
            log("link up" if app.ok else "link DOWN")
        if not app.ok and time.ticks_diff(now, down_at) > 20000:  # stuck (e.g. ENOBUFS with WiFi still associated): reset the network, then the board
            down_at, heals = now, heals + 1
            log("heal %d" % heals)
            if heals >= 3:
                machine.reset()
            poller.close()
            poster.close()
            gc.collect()
            w.disconnect()
            w.connect(cfg["ssid"], cfg["password"])
        if app.ok and app.first:  # whatever is already waiting at boot is not news
            app.first = False
            app.acked = {s["id"]: s["seq"] for s in app.sessions}

        if time.ticks_diff(now, last_usb) > 500:
            last_usb = now
            if axp.r(0x00) & 0x20 != usb:  # unplugging dips the supply and can blank the panel's picture
                usb, redraw_at = axp.r(0x00) & 0x20, time.ticks_add(now, 800)
        if redraw_at and time.ticks_diff(now, redraw_at) > 0 or time.ticks_diff(now, last_redraw) > 30000:
            redraw_at, last_redraw = 0, now
            ui.cache, ui.dirty = {}, True  # rows are cached, so force every one to be drawn again

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
        elif axp.bl and not pend and SLEEP[app.sleep][1] and time.ticks_diff(now, last_input) > SLEEP[app.sleep][1]:
            axp.backlight(False)

        if ui.dirty:
            ui.draw()
        time.sleep_ms(20)
except Exception as e:  # a crash used to leave the board idle with a frozen screen: log it and restart
    with open("crash.log", "a") as f:
        f.write("--- uptime %d s\n" % (time.ticks_ms() // 1000))
        sys.print_exception(e, f)
    time.sleep(1)
    machine.reset()
