#!/usr/bin/env python3
"""Collects Claude Code hook events and serves session state to the pager."""
import json, os, time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("PAGER_HTTP_PORT", 8787))
ASK_WAIT = int(os.environ.get("PAGER_ASK_WAIT", 20))  # seconds the stick has to answer a permission prompt


def load_token():
    """Same token as the stick: env var, else stick/config.json."""
    if "PAGER_TOKEN" in os.environ:
        return os.environ["PAGER_TOKEN"]
    try:
        with open(os.path.join(os.path.dirname(__file__), "..", "stick", "config.json")) as f:
            return json.load(f).get("token", "")
    except (OSError, ValueError):
        return ""


TOKEN = load_token()
ORDER = {"waiting": 0, "done": 1, "working": 2}
STATE_OF = {"UserPromptSubmit": "working", "PostToolUse": "working", "Stop": "done", "Notification": "waiting"}
sessions = {}
asks = {}  # session_id -> {'ev': Event, 'v': None}; one open permission prompt per session
last_seen = 0  # when the stick last polled


def last_text(path):
    """Last assistant text in a transcript JSONL (reads only the tail)."""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 300_000))
            lines = f.read().splitlines()
    except (OSError, TypeError):
        return ""
    for line in reversed(lines):
        try:
            d = json.loads(line)
        except ValueError:
            continue  # first line of the tail may be cut in half
        c = d.get("message", {}).get("content")
        if d.get("type") == "assistant" and isinstance(c, list):
            t = " ".join(b["text"] for b in c if b.get("type") == "text").split()
            if t:
                return " ".join(t)
    return ""


def on_event(d):
    ev, sid = d.get("hook_event_name"), d.get("session_id")
    if ev == "SessionEnd":
        sessions.pop(sid, None)
        return
    st = STATE_OF.get(ev)
    if not st or (ev == "Notification" and d.get("notification_type") == "idle_prompt"):
        return  # idle_prompt only repeats "done"
    s = sessions.setdefault(sid, {"state": None, "seq": 0})
    s["name"] = os.path.basename(d.get("cwd") or "") or "?"
    s["t"] = time.time()
    if ev == "Notification" or st != s["state"]:
        s["seq"] += 1  # new seq = new alert
        s["state"] = st
        s["msg"] = d.get("message", "") if ev == "Notification" else \
            (d.get("last_assistant_message") or last_text(d.get("transcript_path"))) if ev == "Stop" else s.get("msg", "")


def ask_detail(d):
    ti = d.get("tool_input") or {}
    what = ti.get("command") or ti.get("file_path") or ti.get("url") or json.dumps(ti)
    return ("%s: %s" % (d.get("tool_name", "?"), what))[:120]


def hook_reply(v):
    """The PermissionRequest hook stdout for an answer; empty = normal dialog."""
    if v == "allow":
        dec = {"behavior": "allow"}
    elif v == "deny":
        dec = {"behavior": "deny", "message": "Denied from the pager", "interrupt": False}
    else:
        return b""
    return json.dumps({"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": dec}}).encode()


def do_ask(d):
    """Blocks until the stick answers, ASK_WAIT passes, or returns at once if no stick is listening."""
    sid = d.get("session_id")
    if time.time() - last_seen > 15 or sid in asks:  # polls arrive late when the Mac's WiFi stalls
        print(time.strftime("%H:%M:%S"), "ask skipped (stick offline or busy):", ask_detail(d), flush=True)
        return b""
    detail = ask_detail(d)
    on_event({"hook_event_name": "Notification", "session_id": sid, "cwd": d.get("cwd"), "message": detail})
    a = asks[sid] = {"ev": threading.Event(), "v": None}
    sessions[sid]["ask"] = detail
    a["ev"].wait(ASK_WAIT)
    asks.pop(sid, None)
    s = sessions.get(sid)
    if s:
        s.pop("ask", None)
        if a["v"] in ("allow", "deny"):
            s["state"] = "working"  # no PostToolUse follows a deny
    print(time.strftime("%H:%M:%S"), "ask answered:", a["v"], "|", detail, flush=True)
    return hook_reply(a["v"])


def snapshot():
    now = time.time()
    for k in [k for k, s in sessions.items() if now - s["t"] > 6 * 3600]:  # shortcut: crashed sessions never send SessionEnd, so expire after 6h
        del sessions[k]
    rows = [{"id": k, "name": s["name"], "state": s["state"], "seq": s["seq"], "age": int(now - s["t"]), "msg": s["msg"][:1000], "ask": s.get("ask", "")}
            for k, s in sessions.items()]
    return sorted(rows, key=lambda r: (ORDER[r["state"]], r["age"]))


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # the stick keeps one connection open: a new TCP connection every 2 s exhausted its lwIP tables
    timeout = 20  # reap idle connections
    wbufsize = 65536  # headers and body leave in one segment (flushed after each request)

    def reply(self, code, body=b""):
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authed(self):
        return bool(TOKEN) and self.headers.get("X-Token") == TOKEN  # no token configured = stick endpoints stay closed

    def body(self):
        try:
            return json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        except ValueError:
            return None

    def do_POST(self):
        d = self.body()
        if d is None or self.path not in ("/hook", "/ask", "/answer"):
            return self.reply(400 if d is None else 404)
        if self.path == "/answer":
            if not self.authed():
                return self.reply(403)
            a = asks.get(d.get("id"))
            print(time.strftime("%H:%M:%S"), "answer received:", d.get("v"), "open" if a else "NO OPEN REQUEST", flush=True)
            if not a or d.get("v") not in ("allow", "deny", "pass"):
                return self.reply(404)
            a["v"] = d["v"]
            a["ev"].set()
            return self.reply(204)
        if self.client_address[0] not in ("127.0.0.1", "::1"):
            return self.reply(403)  # /hook and /ask come from this Mac's own hooks only
        if self.path == "/ask":
            out = do_ask(d)
            return self.reply(200 if out else 204, out)
        on_event(d)
        print(time.strftime("%H:%M:%S"), d.get("hook_event_name"), d.get("cwd"), flush=True)
        self.reply(204)

    def do_GET(self):
        global last_seen
        if self.path != "/state":
            return self.reply(404)
        if not self.authed():
            return self.reply(403)
        gap = time.time() - last_seen
        if gap > 5:  # polls come every 2 s, so a gap means the stick could not reach us
            print(time.strftime("%H:%M:%S"), "stick polled again after %d s" % gap, flush=True)
        last_seen = time.time()
        self.reply(200, json.dumps(snapshot()).encode())

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
