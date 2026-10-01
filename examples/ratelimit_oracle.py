"""Capture what limits and slowapi do, as the oracle for the `ratelimit` port.

    ../shallowflaws/.venv/bin/python examples/ratelimit_oracle.py

needs `redis-server` on PATH: a throwaway one is started on a free port for
the Redis half and stopped at the end; the backend's own Redis is not
touched.

Four parts, all from the backend's virtualenv (limits 5.8.0, slowapi
0.1.10, redis-py 6.4, FastAPI 0.141 on Starlette 1.6):

1. `limits.parse_many` over a corpus of rate-limit strings — the five the
   backend declares in app/api/rate_limit.py, the grammar's corners, and
   strings it refuses — recording each item's amount, multiples,
   granularity, `repr`, expiry, and `key_for`, or the `ValueError`.
2. `FixedWindowRateLimiter` on `MemoryStorage` driven by scripts of hits,
   tests, window stats, and clears under a frozen `time.time`.
3. The same strategy on `RedisStorage`, through a recording proxy: every
   command redis-py wrote and every reply it read, per call.
4. slowapi's `Limiter` in front of FastAPI routes shaped like the
   backend's, driven by Starlette's test client with a frozen clock and
   chosen client addresses: each response's status, body, and rate-limit
   headers, plus `request.state.view_rate_limit` and its window stats.
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from unittest import mock

# slowapi's Limiter reads RATELIMIT_* from a .env in the working directory
# when one exists; run from an empty directory so none is found.
os.chdir(tempfile.mkdtemp())

import warnings  # noqa: E402

warnings.simplefilter("ignore")

import limits  # noqa: E402
import redis as redis_py  # noqa: E402
import slowapi  # noqa: E402
from fastapi import FastAPI, Request, Response  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from limits import parse_many  # noqa: E402
from limits.storage import MemoryStorage, RedisStorage  # noqa: E402
from limits.strategies import FixedWindowRateLimiter  # noqa: E402
from slowapi import Limiter, _rate_limit_exceeded_handler  # noqa: E402
from slowapi.errors import RateLimitExceeded  # noqa: E402
from slowapi.util import get_remote_address  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

here = os.path.dirname(os.path.abspath(__file__))
T0 = 1790000000.0


def ms(t):
    return int(round(t * 1000))


# ---------------------------------------------------------------------------
# 1. parse_many
# ---------------------------------------------------------------------------

PARSE_CASES = [
    # the backend's own
    "5/minute", "3/hour", "30/minute", "5/hour",
    # every granularity, singular and plural, any case
    "1/second", "2/seconds", "10/day", "10/days", "1/month", "12/months",
    "1/year", "2/years", "7/Minute", "7/HOURS", "7 PER DAY",
    # "per" and multiples
    "10 per hour", "10per hour", "10perhour", "2/3 minutes", "5 per 2 hours",
    "1 per 10seconds", "05/minute", "5/007 minute", "5/0 minute", "0/minute",
    # whitespace
    " 5/minute ", "5 / minute", "5\t/\tminute", "5/minute\n", "\n5/minute",
    "5 per minute", "5/minute　",
    # several
    "1/second; 5/minute", "1/second;5/minute", "1 per day, 2 per month, 3/year",
    "1/second|2/minute", "5/minute:;10/hour", "5/minute :,10/hour",
    "2/second;4/minute", "3/hour; 3/hour",
    # refused
    "", "abc", "5", "5/", "/minute", "minute", "5/minute,", ";5/minute",
    "5/minute;;10/hour", "5/min", "5/s", "5/fortnight", "5 minute",
    "5/-1 minute", "-5/minute", "5.5/minute", "5/minute 10/hour",
    "5/ſecond", "5/mİnute", "5/K", "5/minute:", "5/minutes:",
    "1234567890123456789/minute",
]


def item_record(item):
    return {
        "amount": item.amount,
        "multiples": item.multiples,
        "granularity": item.GRANULARITY.name,
        "repr": repr(item),
        "expiry": item.get_expiry(),
        "key": item.key_for("10.0.0.1", "/api/v1/job_applicant/auth/login"),
    }


parse_out = []
for text in PARSE_CASES:
    try:
        items = parse_many(text)
        parse_out.append({"text": text, "ok": True, "items": [item_record(i) for i in items]})
    except ValueError as e:
        parse_out.append({"text": text, "ok": False, "error": str(e)})
print("parse", len(parse_out), "strings")


# ---------------------------------------------------------------------------
# 2. fixed window on MemoryStorage
# ---------------------------------------------------------------------------

A = ["10.0.0.1", "/api/v1/job_applicant/auth/login"]
B = ["10.0.0.2", "/api/v1/job_applicant/auth/login"]
MEMORY_SCRIPTS = [
    ("login_5_per_minute", [
        ("hit", 0.25, "5/minute", A, 1), ("hit", 1.0, "5/minute", A, 1),
        ("hit", 2.5, "5/minute", A, 1), ("stats", 2.5, "5/minute", A, 1),
        ("hit", 3.0, "5/minute", A, 1), ("hit", 4.0, "5/minute", A, 1),
        ("test", 4.0, "5/minute", A, 1), ("hit", 5.0, "5/minute", A, 1),
        ("stats", 5.0, "5/minute", A, 1), ("hit", 30.0, "5/minute", A, 1),
        ("stats", 30.0, "5/minute", A, 1),
        # one millisecond before the window closes, and at the instant it does
        ("hit", 60.249, "5/minute", A, 1), ("stats", 60.249, "5/minute", A, 1),
        ("test", 60.25, "5/minute", A, 1), ("stats", 60.25, "5/minute", A, 1),
        ("hit", 60.25, "5/minute", A, 1), ("stats", 60.25, "5/minute", A, 1),
    ]),
    ("keys_are_separate", [
        ("hit", 0.0, "1/minute", A, 1), ("hit", 0.0, "1/minute", A, 1),
        ("hit", 0.0, "1/minute", B, 1), ("hit", 0.0, "1/hour", A, 1),
        ("stats", 0.0, "1/minute", B, 1), ("stats", 0.0, "1/hour", A, 1),
        ("stats", 0.0, "1/second", A, 1),
    ]),
    ("cost", [
        ("hit", 0.0, "5/minute", A, 2), ("hit", 0.5, "5/minute", A, 2),
        ("test", 0.5, "5/minute", A, 1), ("test", 0.5, "5/minute", A, 2),
        ("hit", 0.5, "5/minute", A, 2), ("stats", 0.5, "5/minute", A, 1),
        ("hit", 1.0, "5/minute", A, 0), ("test", 1.0, "5/minute", A, 0),
    ]),
    ("cost_zero_opens_a_window", [
        ("hit", 0.0, "2/minute", A, 0), ("stats", 10.0, "2/minute", A, 1),
        ("hit", 20.0, "2/minute", A, 1), ("stats", 20.0, "2/minute", A, 1),
        ("hit", 60.0, "2/minute", A, 1), ("stats", 60.0, "2/minute", A, 1),
    ]),
    ("cost_above_amount", [
        ("hit", 0.0, "3/minute", A, 4), ("stats", 0.0, "3/minute", A, 1),
        ("test", 0.0, "3/minute", A, 1), ("hit", 0.0, "3/minute", A, 1),
    ]),
    ("clear", [
        ("hit", 0.0, "1/hour", A, 1), ("hit", 1.0, "1/hour", A, 1),
        ("clear", 2.0, "1/hour", A, 1), ("stats", 2.0, "1/hour", A, 1),
        ("hit", 3.0, "1/hour", A, 1), ("stats", 3.0, "1/hour", A, 1),
    ]),
    ("multiples", [
        ("hit", 0.0, "2/3 seconds", A, 1), ("hit", 1.0, "2/3 seconds", A, 1),
        ("hit", 2.999, "2/3 seconds", A, 1), ("stats", 2.999, "2/3 seconds", A, 1),
        ("hit", 3.0, "2/3 seconds", A, 1), ("stats", 3.0, "2/3 seconds", A, 1),
    ]),
    ("zero_amount", [
        ("test", 0.0, "0/minute", A, 1), ("hit", 0.0, "0/minute", A, 1),
        ("stats", 0.0, "0/minute", A, 1),
    ]),
    ("long_windows", [
        ("hit", 0.0, "1/year", A, 1), ("hit", 86400.0 * 359, "1/year", A, 1),
        ("stats", 86400.0 * 359, "1/year", A, 1), ("hit", 86400.0 * 360, "1/year", A, 1),
        ("hit", 0.0, "1/month", B, 1), ("stats", 0.0, "1/month", B, 1),
    ]),
]

memory_out = []
for name, ops in MEMORY_SCRIPTS:
    storage = MemoryStorage()
    strategy = FixedWindowRateLimiter(storage)
    results = []
    for op, dt, limit_text, ids, cost in ops:
        item = parse_many(limit_text)[0]
        with mock.patch("time.time", return_value=T0 + dt):
            if op == "hit":
                r = strategy.hit(item, *ids, cost=cost)
            elif op == "test":
                r = strategy.test(item, *ids, cost=cost)
            elif op == "stats":
                s = strategy.get_window_stats(item, *ids)
                r = [ms(s.reset_time), s.remaining]
            else:
                strategy.clear(item, *ids)
                r = None
        results.append({"op": op, "at": ms(T0 + dt), "limit": limit_text, "ids": ids, "cost": cost, "result": r})
    storage.timer.cancel()
    memory_out.append({"name": name, "ops": results})
print("memory", len(memory_out), "scripts")


# ---------------------------------------------------------------------------
# 3. fixed window on RedisStorage, through a recording proxy
# ---------------------------------------------------------------------------

def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Proxy:
    """Forwards one TCP connection at a time, recording both directions."""

    def __init__(self, upstream_port):
        self.upstream_port = upstream_port
        self.sent = bytearray()
        self.received = bytearray()
        self.lock = threading.Lock()
        self.listener = socket.socket()
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(4)
        self.port = self.listener.getsockname()[1]
        threading.Thread(target=self.accept, daemon=True).start()

    def accept(self):
        while True:
            try:
                down, _ = self.listener.accept()
            except OSError:
                return
            up = socket.create_connection(("127.0.0.1", self.upstream_port))
            threading.Thread(target=self.pump, args=(down, up, self.sent), daemon=True).start()
            threading.Thread(target=self.pump, args=(up, down, self.received), daemon=True).start()

    def pump(self, src, dst, log):
        while True:
            try:
                data = src.recv(65536)
            except OSError:
                return
            if not data:
                return
            with self.lock:
                log.extend(data)
            dst.sendall(data)

    def mark(self):
        with self.lock:
            return len(self.sent), len(self.received)


def resp_split(buf):
    """Split a RESP2 byte stream into whole frames."""
    frames = []
    at = 0

    def frame_end(i):
        nl = buf.index(b"\r\n", i)
        kind = buf[i:i + 1]
        if kind in (b"+", b"-", b":"):
            return nl + 2
        n = int(buf[i + 1:nl])
        if kind == b"$":
            return nl + 2 if n < 0 else nl + 2 + n + 2
        j = nl + 2
        for _ in range(max(n, 0)):
            j = frame_end(j)
        return j

    while at < len(buf):
        end = frame_end(at)
        frames.append(bytes(buf[at:end]).decode("latin-1"))
        at = end
    return frames


redis_dir = tempfile.mkdtemp()
redis_port = free_port()
server = subprocess.Popen(
    [shutil.which("redis-server"), "--port", str(redis_port), "--bind", "127.0.0.1",
     "--save", "", "--appendonly", "no", "--dir", redis_dir],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)
for _ in range(100):
    try:
        socket.create_connection(("127.0.0.1", redis_port)).close()
        break
    except OSError:
        time.sleep(0.05)
redis_version = redis_py.Redis(port=redis_port).info()["redis_version"]
proxy = Proxy(redis_port)

storage = RedisStorage(f"redis://127.0.0.1:{proxy.port}")
strategy = FixedWindowRateLimiter(storage)
login = parse_many("5/minute")[0]
reset = parse_many("3/hour")[0]
REDIS_SCRIPT = [
    ("check", None, None, None),
    ("hit", login, A, 1), ("hit", login, A, 1), ("hit", login, A, 1),
    ("hit", login, A, 1), ("hit", login, A, 1), ("hit", login, A, 1),
    ("stats", login, A, None), ("test", login, A, 1),
    ("hit", reset, B, 3), ("test", reset, B, 1), ("hit", reset, B, 1), ("stats", reset, B, None),
    ("clear", login, A, None), ("stats", login, A, None), ("test", login, A, 1),
]
redis_out = []
for op, item, ids, cost in REDIS_SCRIPT:
    before = proxy.mark()
    with mock.patch("time.time", return_value=T0):
        if op == "check":
            r = storage.check()
        elif op == "hit":
            r = strategy.hit(item, *ids, cost=cost)
        elif op == "test":
            r = strategy.test(item, *ids, cost=cost)
        elif op == "stats":
            s = strategy.get_window_stats(item, *ids)
            r = [ms(s.reset_time), s.remaining]
        else:
            strategy.clear(item, *ids)
            r = None
    time.sleep(0.05)
    after = proxy.mark()
    redis_out.append({
        "op": op,
        "limit": repr(item) if item else None,
        "ids": ids,
        "cost": cost,
        "result": r,
        "commands": resp_split(proxy.sent[before[0]:after[0]]),
        "replies": resp_split(proxy.received[before[1]:after[1]]),
    })
server.terminate()
server.wait()
shutil.rmtree(redis_dir, ignore_errors=True)
print("redis", len(redis_out), "calls against redis-server", redis_version)


# ---------------------------------------------------------------------------
# 4. slowapi in front of FastAPI
# ---------------------------------------------------------------------------

state_log = []


def observe(limiter):
    """Record `view_rate_limit` and its window stats after each request."""

    async def middleware(request: Request, call_next):
        response = await call_next(request)
        view = getattr(request.state, "view_rate_limit", None)
        if view is None:
            state_log.append(None)
        else:
            s = limiter.limiter.get_window_stats(view[0], *view[1])
            state_log.append({"limit": repr(view[0]), "args": list(view[1]), "reset": ms(s.reset_time), "remaining": s.remaining})
        return response

    return middleware


def backend_app():
    """The backend's shape: remote address, fixed window, no headers, its
    five limit strings on routes at the backend's paths."""
    limiter = Limiter(key_func=get_remote_address, storage_uri="memory://", strategy="fixed-window")
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.middleware("http")(observe(limiter))

    @app.post("/api/v1/job_applicant/auth/login")
    @limiter.limit("5/minute")
    async def applicant_login(request: Request):
        return {"ok": True}

    @app.post("/api/v1/employer/auth/login")
    @limiter.limit("5/minute")
    async def employer_login(request: Request):
        return {"ok": True}

    @app.post("/api/v1/job_applicant/auth/request_password_reset")
    @limiter.limit("3/hour")
    async def request_password_reset(request: Request):
        return {"success": True}

    @app.post("/api/v1/employer/auth/verify_email")
    @limiter.limit("5/hour")
    async def verify_email(request: Request):
        return {"success": True}

    @app.post("/api/v1/employer/documents/extract_text")
    @limiter.limit("30/minute")
    def extract_text(request: Request):
        return {"text": ""}

    @app.get("/jobs/{job_id}")
    @limiter.limit("2/minute")
    async def job(request: Request, job_id: int):
        return {"job_id": job_id}

    @app.get("/open")
    async def open_route():
        return {"open": True}

    return app, limiter


def knobs_app():
    """The rest of `limit` and `shared_limit`, with headers on and a key prefix."""
    limiter = Limiter(key_func=get_remote_address, headers_enabled=True, key_prefix="app")
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.middleware("http")(observe(limiter))

    @app.get("/multi")
    @limiter.limit("2/second;5/minute")
    async def multi(request: Request, response: Response):
        return {"multi": True}

    @app.get("/stacked")
    @limiter.limit("3/minute")
    @limiter.limit("1/second")
    async def stacked(request: Request):
        return JSONResponse({"stacked": True})

    @app.get("/shared/a")
    @limiter.shared_limit("3/minute", scope="pair")
    async def shared_a(request: Request, response: Response):
        return {"a": True}

    @app.get("/shared/b")
    @limiter.shared_limit("3/minute", scope="pair")
    async def shared_b(request: Request, response: Response):
        return {"b": True}

    @app.api_route("/method", methods=["GET", "POST"])
    @limiter.limit("2/minute", per_method=True)
    async def per_method(request: Request, response: Response):
        return {"method": request.method}

    @app.api_route("/only_post", methods=["GET", "POST"])
    @limiter.limit("1/minute", methods=["POST"])
    async def only_post(request: Request, response: Response):
        return {"method": request.method}

    @app.get("/costly")
    @limiter.limit("5/minute", cost=2)
    async def costly(request: Request, response: Response):
        return {"costly": True}

    @app.get("/custom")
    @limiter.limit("1/minute", error_message="Slow down")
    async def custom(request: Request, response: Response):
        return {"custom": True}

    return app, limiter


def endpoint_app():
    """key_style="endpoint": the scope is the function, not the path."""
    limiter = Limiter(key_func=get_remote_address, key_style="endpoint")
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.middleware("http")(observe(limiter))

    @app.get("/jobs/{job_id}")
    @limiter.limit("2/minute")
    async def job(request: Request, job_id: int):
        return {"job_id": job_id}

    return app, limiter


def disabled_app():
    limiter = Limiter(key_func=get_remote_address, enabled=False)
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.middleware("http")(observe(limiter))

    @app.post("/api/v1/job_applicant/auth/login")
    @limiter.limit("1/minute")
    async def applicant_login(request: Request):
        return {"ok": True}

    return app, limiter


LOGIN = "/api/v1/job_applicant/auth/login"
EMP_LOGIN = "/api/v1/employer/auth/login"
RESET = "/api/v1/job_applicant/auth/request_password_reset"
VERIFY = "/api/v1/employer/auth/verify_email"
EXTRACT = "/api/v1/employer/documents/extract_text"
IP1, IP2 = "203.0.113.7", "198.51.100.23"

APPS = [
    ("backend", backend_app, [
        *[(IP1, "POST", LOGIN, 0.25 + i) for i in range(6)],
        (IP2, "POST", LOGIN, 6.5),
        (IP1, "POST", EMP_LOGIN, 7.0),
        (IP1, "POST", LOGIN, 60.0),
        (IP1, "POST", LOGIN, 60.25),
        (IP1, "POST", LOGIN, 61.0),
        *[(IP1, "POST", RESET, 100.0 + i) for i in range(4)],
        (IP1, "POST", RESET, 3600.0 + 100.0),
        *[(IP2, "POST", VERIFY, 5.0 + 60 * i) for i in range(6)],
        *[(IP2, "POST", EXTRACT, 200.0 + 0.5 * i) for i in range(31)],
        (IP1, "GET", "/jobs/1", 300.0), (IP1, "GET", "/jobs/1", 300.0),
        (IP1, "GET", "/jobs/1", 300.0), (IP1, "GET", "/jobs/2", 300.0),
        (IP1, "GET", "/open", 300.0), (IP1, "GET", "/missing", 300.0),
        ("", "POST", LOGIN, 400.0), ("", "POST", LOGIN, 400.0),
    ]),
    ("knobs", knobs_app, [
        (IP1, "GET", "/multi", 0.25), (IP1, "GET", "/multi", 0.5),
        (IP1, "GET", "/multi", 0.75), (IP1, "GET", "/multi", 1.25),
        (IP1, "GET", "/multi", 1.5), (IP1, "GET", "/multi", 2.5),
        (IP1, "GET", "/multi", 3.5), (IP1, "GET", "/multi", 4.5),
        (IP1, "GET", "/stacked", 10.0), (IP1, "GET", "/stacked", 10.5),
        (IP1, "GET", "/stacked", 11.0), (IP1, "GET", "/stacked", 12.0),
        (IP1, "GET", "/stacked", 13.0),
        (IP1, "GET", "/shared/a", 20.0), (IP1, "GET", "/shared/b", 20.0),
        (IP1, "GET", "/shared/a", 20.0), (IP1, "GET", "/shared/b", 20.0),
        (IP2, "GET", "/shared/b", 20.0),
        (IP1, "GET", "/method", 30.0), (IP1, "POST", "/method", 30.0),
        (IP1, "GET", "/method", 30.0), (IP1, "GET", "/method", 30.0),
        (IP1, "POST", "/method", 30.0), (IP1, "POST", "/method", 30.0),
        (IP1, "GET", "/only_post", 40.0), (IP1, "GET", "/only_post", 40.0),
        (IP1, "POST", "/only_post", 40.0), (IP1, "POST", "/only_post", 40.0),
        (IP1, "GET", "/only_post", 40.0),
        (IP1, "GET", "/costly", 50.0), (IP1, "GET", "/costly", 50.0),
        (IP1, "GET", "/costly", 50.0), (IP1, "GET", "/costly", 50.0),
        (IP1, "GET", "/custom", 60.1), (IP1, "GET", "/custom", 60.2),
    ]),
    ("endpoint", endpoint_app, [
        (IP1, "GET", "/jobs/1", 0.0), (IP1, "GET", "/jobs/2", 0.0),
        (IP1, "GET", "/jobs/3", 0.0), (IP2, "GET", "/jobs/3", 0.0),
    ]),
    ("disabled", disabled_app, [
        (IP1, "POST", LOGIN, 0.0), (IP1, "POST", LOGIN, 0.0), (IP1, "POST", LOGIN, 0.0),
    ]),
]

HEADERS = ["content-type", "x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset", "retry-after"]

apps_out = []
for app_name, build, requests in APPS:
    app, limiter = build()
    clients = {}
    cases = []
    for host, method, path, dt in requests:
        client = clients.get(host)
        if client is None:
            client = clients[host] = TestClient(app, client=(host, 50000))
        del state_log[:]
        with mock.patch("time.time", return_value=T0 + dt):
            r = client.request(method, path)
        headers = [[h, r.headers[h]] for h in HEADERS if h in r.headers]
        cases.append({
            "host": host, "method": method, "path": path, "at": ms(T0 + dt),
            "status": r.status_code, "body": r.text, "headers": headers,
            "view": state_log[0] if state_log else None,
        })
    limiter._storage.timer.cancel()
    apps_out.append({"name": app_name, "cases": cases})
    print("app", app_name, len(cases), "requests")

versions = {
    "limits": limits.__version__,
    "slowapi": "0.1.10",
    "redis-py": redis_py.__version__,
    "redis-server": redis_version,
    "python": sys.version.split()[0],
}
out = {"versions": versions, "t0": ms(T0), "parse": parse_out, "memory": memory_out, "redis": redis_out, "apps": apps_out}
with open(os.path.join(here, "ratelimit_oracle.json"), "w") as f:
    json.dump(out, f, indent=1, ensure_ascii=False)
    f.write("\n")
print("wrote examples/ratelimit_oracle.json")
