import errno, network, select, socket
from time import ticks_ms, ticks_diff


def wifi():
    return network.WLAN(network.STA_IF)


_addr = {}


def resolve(host, port):
    """getaddrinfo once: it needs scarce network memory and failed (EAI_MEMORY) exactly when the link was already struggling."""
    if (host, port) not in _addr:
        _addr[(host, port)] = socket.getaddrinfo(host, port)[0][-1]
    return _addr[(host, port)]


class Fetch:
    """Non-blocking HTTP/1.1 request: start() it, then call step() every loop tick until it returns (status, body).
    keep=True reuses one socket between requests (the polling client); keep=False closes after each (one-off posts)."""
    TIMEOUT = 6000  # ms; the Mac's WiFi link stalls for 1-2 s now and then

    def __init__(self, keep=False):
        self.keep, self.s, self.busy = keep, None, False

    def close(self):
        if self.s:
            self.s.close()
            self.s = None
        self.busy = False

    def start(self, host, port, token, method, path, body=b"", fresh=False):
        if fresh:
            self.close()
        self.args = (host, port, token, method, path, body)
        self.out = ("%s %s HTTP/1.1\r\nHost: pager\r\nX-Token: %s\r\nContent-Length: %d\r\nConnection: %s\r\n\r\n" % (
            method, path, token, len(body), "keep-alive" if self.keep else "close")).encode() + body
        self.buf, self.t, self.busy, self.reused = b"", ticks_ms(), True, self.s is not None
        if self.s is None:
            s = self.s = socket.socket()
            s.setblocking(False)
            self.poll = select.poll()
            self.poll.register(s, select.POLLIN | select.POLLOUT)
            try:
                s.connect(resolve(host, port))
            except OSError as e:
                if e.args[0] != errno.EINPROGRESS:
                    self.close()
                    raise

    def step(self):
        """None while pending; (status, body) when done; OSError on failure or timeout."""
        if ticks_diff(ticks_ms(), self.t) > self.TIMEOUT:
            self.close()
            raise OSError("timeout")
        try:
            for _, ev in self.poll.poll(0):
                if ev & select.POLLERR:
                    raise OSError("socket error")
                if self.out:
                    if ev & select.POLLOUT:
                        self.out = self.out[self.s.send(self.out):]
                elif ev & (select.POLLIN | select.POLLHUP):
                    c = self.s.recv(1024)
                    if not c:
                        raise OSError("closed")
                    self.buf += c
                    i = self.buf.find(b"\r\n\r\n")
                    if i >= 0:
                        n = int(self.buf[:i].lower().split(b"content-length:")[1].split(b"\r\n")[0])
                        if len(self.buf) - i - 4 >= n:
                            status, body = int(self.buf.split(b" ")[1]), self.buf[i + 4:i + 4 + n]
                            self.busy = False
                            if not self.keep:
                                self.close()
                            return status, body
        except OSError:
            self.close()
            if self.reused and not self.buf:  # a reused connection the server had already closed: retry once on a new one
                self.start(*self.args, fresh=True)
                return None
            raise
