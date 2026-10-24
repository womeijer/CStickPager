"""M5StickC Plus hardware: ST7789 display, AXP192 power chip, buttons, LED, buzzer."""
import framebuf, struct
from machine import Pin, SPI, I2C, PWM
from time import sleep_ms, ticks_ms, ticks_diff

W, H = 240, 135  # landscape


def rgb(r, g, b):
    """RGB565, byte-swapped so it works both as framebuf colour and as raw panel bytes."""
    c = (r & 0xF8) << 8 | (g & 0xFC) << 3 | b >> 3
    return (c & 0xFF) << 8 | c >> 8


class Display:
    memo = {}

    def __init__(self):
        self.spi = SPI(1, baudrate=27_000_000, sck=Pin(13), mosi=Pin(15))
        self.cs, self.dc = Pin(5, Pin.OUT, value=1), Pin(23, Pin.OUT)
        rst = Pin(18, Pin.OUT, value=0)
        sleep_ms(50)
        rst(1)
        sleep_ms(120)
        # M5Stack's ST7789 setup for the StickC Plus (voltages + gamma): panels vary between batches and the bare defaults can be unstable
        for cmd, data, wait in ((0x01, b"", 150),
                                (0xB7, b"\x35", 0), (0xBB, b"\x28", 0), (0xC0, b"\x0C", 0), (0xC2, b"\x01\xFF", 0),
                                (0xC3, b"\x10", 0), (0xC4, b"\x20", 0), (0xD0, b"\xA4\xA1", 0), (0xB0, b"\x00\xC0", 0),
                                (0xE0, b"\xD0\x00\x02\x07\x0A\x28\x32\x44\x42\x06\x0E\x12\x14\x17", 0),
                                (0xE1, b"\xD0\x00\x02\x07\x0A\x28\x31\x54\x47\x0E\x1C\x17\x1B\x1E", 0),
                                (0x11, b"", 130), (0x3A, b"\x55", 0), (0x36, b"\x60", 0),
                                (0x21, b"", 0), (0x13, b"", 10), (0x38, b"", 0), (0x29, b"", 100)):
            self.cmd(cmd, data)
            sleep_ms(wait)

    def cmd(self, c, data=b""):
        self.cs(0)
        self.dc(0)
        self.spi.write(bytes([c]))
        self.dc(1)
        if data:
            self.spi.write(data)
        self.cs(1)

    def blit(self, x, y, w, h, buf):
        x, y = x + 40, y + 53  # panel offset for the 135x240 glass in landscape
        self.cmd(0x2A, struct.pack(">HH", x, x + w - 1))
        self.cmd(0x2B, struct.pack(">HH", y, y + h - 1))
        self.cs(0)
        self.dc(0)
        self.spi.write(b"\x2c")
        self.dc(1)
        self.spi.write(buf)
        self.cs(1)

    def rect(self, x, y, w, h, c):
        row = struct.pack("<H", c) * w  # one row at a time: a full-screen buffer (65KB) does not fit in RAM
        for i in range(h):
            self.blit(x, y + i, w, 1, row)

    def glyph_row(self, b, fg, bg, k):
        """One 8-pixel row of a glyph scaled k times horizontally, memoised (the per-pixel loop was ~70 ms a line)."""
        key = (b, fg, bg, k)
        v = self.memo.get(key)
        if v is None:
            if len(self.memo) > 500:
                self.memo = {}
            on, off = struct.pack("<H", fg) * k, struct.pack("<H", bg) * k
            v = self.memo[key] = b"".join(on if b & (0x80 >> i) else off for i in range(8))
        return v

    def text(self, t, x, y, fg, bg=0, k=1):
        n = len(t)
        w = n * 8
        if k == 1:
            buf = bytearray(w * 16)
            fb = framebuf.FrameBuffer(buf, w, 8, framebuf.RGB565)
            fb.fill(bg)
            fb.text(t, 0, 0, fg)
        else:
            mono = bytearray(w)  # 1 bit per pixel: row r, char c is mono[r * n + c]
            fb = framebuf.FrameBuffer(mono, w, 8, framebuf.MONO_HLSB)
            fb.text(t, 0, 0, 1)
            buf = bytearray()
            for r in range(8):
                buf += b"".join(self.glyph_row(mono[r * n + c], fg, bg, k) for c in range(n)) * k
        self.blit(x, y, w * k, 8 * k, buf)


class AXP:
    def __init__(self):
        self.i2c = I2C(0, scl=Pin(22), sda=Pin(21), freq=400_000)
        self.w(0x28, 0xCC)  # LDO2 (backlight) + LDO3 at 3.0V
        self.w(0x42, 0x03)  # enable power-button click/long-press flags
        self.w(0x46, 0xFF)
        self.backlight(True)

    def r(self, reg):
        return self.i2c.readfrom_mem(0x34, reg, 1)[0]

    def w(self, reg, v):
        self.i2c.writeto_mem(0x34, reg, bytes([v]))

    def brightness(self, i):
        """i in 0..2. LDO2 (backlight) volts = 1.8 + 0.1 * level, so level 8 (2.6V) is dim but never off."""
        self.w(0x28, ((8, 11, 14)[i] << 4) | 0x0C)

    def backlight(self, on):
        v = self.r(0x12)
        self.w(0x12, v | 4 if on else v & ~4)
        self.bl = on

    def click(self):
        f = self.r(0x46)
        if f:
            self.w(0x46, f)
        return bool(f & 2)

    def volts(self):
        return ((self.r(0x78) << 4) | (self.r(0x79) & 0xF)) * 1.1 / 1000


class Buttons:
    """poll() -> 'next' (side B), 'select' (front A), 'back' (power click) or None."""

    def __init__(self, axp):
        self.axp, self.a, self.b = axp, Pin(37, Pin.IN), Pin(39, Pin.IN)
        self.prev, self.last = (1, 1), 0

    def poll(self):
        cur = (self.a(), self.b())
        prev, self.prev = self.prev, cur
        press = "select" if prev[0] and not cur[0] else "next" if prev[1] and not cur[1] else "back" if self.axp.click() else None
        if press and ticks_diff(ticks_ms(), self.last) > 150:  # debounce
            self.last = ticks_ms()
            return press


class Alert:
    def __init__(self):
        self.led = Pin(10, Pin.OUT, value=1)  # active low
        self.buz = PWM(Pin(2), freq=2000, duty=0)

    def beep(self, n):
        for _ in range(n):
            self.buz.duty(512)
            sleep_ms(70)
            self.buz.duty(0)
            sleep_ms(70)

    def blink(self, mode):
        """mode: 'urgent' fast blink, 'gentle' slow pulse, None off."""
        t = ticks_ms()
        self.led(0 if (mode == "urgent" and t // 125 % 2) or (mode == "gentle" and t % 1500 < 150) else 1)
