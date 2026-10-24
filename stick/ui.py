"""Screen stack. A screen has draw(ui), next(ui), select(ui), back(ui). Add a screen = push() one."""
from hw import rgb, W, H

BG, FG, DIM, HDR = 0, rgb(255, 255, 255), rgb(140, 140, 140), rgb(30, 30, 60)
SEL = rgb(40, 70, 140)
COL = {"working": rgb(90, 160, 255), "waiting": rgb(255, 80, 80), "done": rgb(90, 220, 110)}
READ = {"working": rgb(50, 90, 140), "waiting": rgb(140, 50, 50), "done": rgb(50, 120, 60)}
MARK = {"working": "~", "waiting": "!", "done": "+"}
ROW = 18


class UI:
    def __init__(self, d, app, root):
        self.d, self.app, self.stack, self.dirty = d, app, [root], True
        self.reset()

    def reset(self):
        self.d.rect(0, 0, W, H, BG)
        self.cache = {}
        self.dirty = True

    def push(self, scr):
        self.stack.append(scr)
        self.reset()

    def pop(self):
        if len(self.stack) > 1:
            self.stack.pop()
            self.reset()

    def line(self, y, t, fg=FG, bg=BG, k=2, dot=False):
        cells = W // (8 * k)
        n = cells - 1 if dot else cells  # last cell is reserved for the dot
        t = t[:n] + " " * (n - len(t))
        if self.cache.get(y) != (t, fg, bg, k, dot):  # skip unchanged rows: SPI + scaling is slow
            self.cache[y] = (t, fg, bg, k, dot)
            self.d.text(t + " " * (cells - n), 0, y, fg, bg, k)
            if dot:
                x, y = W - 8 * k + 2, y + 4  # octagon from three overlapping rects
                self.d.rect(x + 2, y, 4, 8, FG)
                self.d.rect(x, y + 2, 8, 4, FG)
                self.d.rect(x + 1, y + 1, 6, 6, FG)

    def header(self, title):
        v = self.app.axp.volts()
        bars = sum(v >= t for t in (3.55, 3.7, 3.85, 4.0))  # rough LiPo curve; reads high while charging
        t = (title + " " * 26)[:26]
        if not self.app.ok:
            t = t[:20] + "NOLINK"
        if self.cache.get("hdr") != (t, bars):
            self.cache["hdr"] = (t, bars)
            d = self.d
            d.rect(0, 0, W, 5, HDR)
            d.rect(0, 13, W, 5, HDR)
            d.text(t, 0, 5, FG, HDR, 1)
            d.rect(208, 5, 32, 8, HDR)
            for i in range(4):
                d.rect(212 + i * 6, 5, 5, 8, (FG if bars > 1 else rgb(255, 80, 80)) if i < bars else rgb(70, 70, 100))

    def draw(self):
        self.stack[-1].draw(self)
        self.dirty = False

    def key(self, k):
        getattr(self.stack[-1], k)(self)
        self.dirty = True


class Menu:
    """items(app) -> [(text, colour, action(ui) or None)]; extend by returning more rows."""

    def __init__(self, title, items, back=None):
        self.title, self.items, self.sel, self.on_back = title, items, 0, back

    def draw(self, ui):
        its = self.items(ui.app)
        self.sel = min(self.sel, len(its) - 1)
        top = max(0, self.sel - 5)
        ui.header(self.title(ui.app) if callable(self.title) else self.title)
        for i in range(6):
            y = ROW + 2 + i * ROW
            if top + i < len(its):
                t, c, _, dot = (its[top + i] + (False,))[:4]
                ui.line(y, t, c, SEL if top + i == self.sel else BG, 2, dot)
            else:
                ui.line(y, "")

    def next(self, ui):
        self.sel = (self.sel + 1) % len(self.items(ui.app))

    def select(self, ui):
        act = self.items(ui.app)[self.sel][2]
        if act:
            act(ui)

    def back(self, ui):
        self.on_back(ui) if self.on_back else ui.pop()


def wrap(t, n=30):
    """Word-wrap to lines of at most n chars."""
    lines, cur = [], ""
    for w in t.split():
        while len(w) > n:  # a word longer than a line is split
            if cur:
                lines.append(cur)
                cur = ""
            lines.append(w[:n])
            w = w[n:]
        if cur and len(cur) + 1 + len(w) > n:
            lines.append(cur)
            cur = w
        else:
            cur = cur + " " + w if cur else w
    if cur:
        lines.append(cur)
    return lines


PAGE, COLS = 6, 15  # message lines per screen at double size, chars per line


class Detail:
    """One session. With an open permission prompt it becomes Allow / Deny (back = leave it to the Mac's dialog)."""

    def __init__(self, sid):
        self.sid, self.i, self.off, self.seq, self.lines = sid, 0, 0, None, []

    def sess(self, ui):
        return next((s for s in ui.app.sessions if s["id"] == self.sid), None)

    def draw(self, ui):
        s = self.sess(ui)
        if not s:
            ui.header("ended")
            ui.line(ROW + 2, "session ended", DIM)
            return
        ui.app.acked[self.sid] = s["seq"]
        ui.header(s["name"])
        if s.get("ask"):
            for i in range(4):
                ui.line(ROW + 2 + i * 10, s["ask"][i * 30:i * 30 + 30], FG, BG, 1)
            for j, o in enumerate(("Allow", "Deny")):
                ui.line(ROW + 52 + j * 22, o, COL["done"] if j == 0 else COL["waiting"], SEL if j == self.i else BG)
            return
        if s["seq"] != self.seq:  # new message: wrap it once and start at the top
            self.seq, self.off, self.lines = s["seq"], 0, wrap(s["msg"], COLS)
        pages = max(1, (len(self.lines) + PAGE - 1) // PAGE)
        age = "%dm ago" % (s["age"] // 60) if s["age"] >= 60 else "%ds ago" % s["age"]
        pg = "%d/%d" % (self.off // PAGE + 1, pages) if pages > 1 else ""
        info = s["state"].upper() + " " + age
        ui.line(ROW + 2, info + " " * (30 - len(info) - len(pg)) + pg, COL[s["state"]], BG, 1)
        for i in range(PAGE):
            ui.line(ROW + 14 + i * 16, (self.lines[self.off + i] if self.off + i < len(self.lines) else ""))

    def asking(self, ui):
        s = self.sess(ui)
        return s and s.get("ask")

    def next(self, ui):
        if self.asking(ui):
            self.i = 1 - self.i
        else:  # reading: side button leaves, like the power click
            ui.pop()

    def select(self, ui):
        if self.asking(ui):
            ui.app.answer(self.sid, "deny" if self.i else "allow")
            ui.pop()
        else:  # next page, wrapping to the top; leave with back
            self.off = self.off + PAGE if self.off + PAGE < len(self.lines) else 0

    def back(self, ui):
        if self.asking(ui):
            ui.app.answer(self.sid, "pass")
        ui.pop()
