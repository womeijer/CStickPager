import errno, network, select, socket
from time import ticks_ms, ticks_diff


def wifi():
    return network.WLAN(network.STA_IF)


class Fetch:
    """Non-blocking HTTP/1.0 request: start() it, then call step() every loop tick until it returns (status, body)."""
    TIMEOUT = 6000  # ms; the Mac's WiFi link stalls for 1-2 s now and then

    def __init__(self):
        self.s = None

    def close(self):
        if self.s:
            self.s.close()
            self.s = None

    def start(self, host, port, token, method, path, body=b""):
        self.close()
        self.out = ("%s %s HTTP/1.0\r\nX-Token: %s\r\nContent-Length: %d\r\n\r\n" % (method, path, token, len(body))).encode() + body
        self.buf, self.t = b"", ticks_ms()
        s = self.s = socket.socket()
        s.setblocking(False)
        self.poll = select.poll()
        self.poll.register(s, select.POLLIN | select.POLLOUT)
        try:
            s.connect(socket.getaddrinfo(host, port)[0][-1])
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
                    if c:
                        self.buf += c
                    else:
                        return self.finish()
        except OSError:
            self.close()
            raise

    def finish(self):
        buf = self.buf
        self.close()
        head, _, body = buf.partition(b"\r\n\r\n")
        if not head:
            raise OSError("empty reply")
        return int(head.split(b" ")[1]), body
