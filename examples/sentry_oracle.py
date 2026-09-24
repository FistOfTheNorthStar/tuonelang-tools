"""Capture what Python's logging formats and what sentry-sdk sends, as the
oracle for the `log` and `sentry` ports.

    ../shallowflaws/.venv/bin/python examples/sentry_oracle.py

Runs the backend's own sentry-sdk (2.69) with a transport that keeps the
envelopes instead of sending them, and Python's logging with the two
formats app/main.py configures, and writes examples/sentry_oracle.json.

What is Python's rather than the protocol's is stripped from each event
before it is recorded: the installed `modules`, `sys.argv`, the CPython
`runtime` context, the `sdk` block, `platform`, and everything in a
stack frame except its name, module, file, line, and in-app flag.
Random ids and timestamps are replaced by fixed values so the port can
be given the same. The envelope's item `length` is recomputed for the
reduced payload. Everything else — key order, nesting, the logging
integration's breadcrumbs and events, mechanisms — is what the SDK
produced.
"""

import json
import logging
import os
import sys
import time

import sentry_sdk
from sentry_sdk.transport import Transport

FIXED_TIME = "2026-09-22T08:30:00.123456Z"
FIXED_SENT_AT = "2026-09-22T08:30:01.000000Z"
DSN = "https://abcdef0123456789abcdef0123456789@o123456.ingest.de.sentry.io/4507000000000000"
EVENT_IDS = ["%02d" % k + "23456789abcdef0123456789abcdef" for k in range(10)]
FIXED_ASCTIME = "2026-09-22 08:30:00,123"
TRACE_ID = "b8d6951380264e93b77bccfe8164cca5"

envelopes = []


class Keep(Transport):
    def __init__(self, options=None):
        super().__init__(options)

    def capture_envelope(self, envelope):
        envelopes.append(envelope.serialize().decode())

    def flush(self, timeout, callback=None):
        pass

    def kill(self):
        pass


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", stream=open(os.devnull, "w"))
sentry_sdk.init(dsn=DSN, environment="production", server_name="cloud", release="shallowflaws@2026.09.22", traces_sample_rate=0.0, send_default_pii=False, transport=Keep, auto_enabling_integrations=False)

log = logging.getLogger("app.services.email")
tasks = logging.getLogger("app.tasks.extraction_tasks")

sentry_sdk.capture_message("Worker started")
log.info("[SES] Email sent to %s, MessageId: %s", "a@b.co", "0100019")
log.debug("not a breadcrumb: below the root level")
tasks.warning("retrying job %d after %s", 7, "timeout")
sentry_sdk.set_tag("job_id", "7")
sentry_sdk.capture_message("Extraction finished")
sentry_sdk.add_breadcrumb(category="auth", message="login", level="info", data={"user_id": 42})
try:
    {}["missing"]
except Exception:
    tasks.exception("Extraction failed")
sentry_sdk.set_user({"id": "42"})
sentry_sdk.capture_exception(ValueError("bad input"))
tasks.error("worker gave up after %d attempts", 3)
sentry_sdk.capture_message("Quota nearly exhausted", level="warning")
tasks.critical("out of %s", "memory")
sentry_sdk.get_client().flush()


def reduce_event(e):
    e.pop("modules", None)
    e.pop("sdk", None)
    e.pop("platform", None)
    e.pop("extra", None)
    e.get("contexts", {}).pop("runtime", None)
    if "trace" in e.get("contexts", {}):
        e["contexts"]["trace"] = {"trace_id": TRACE_ID, "span_id": "9a99b3dbf38b91a0", "parent_span_id": None}
    for value in e.get("exception", {}).get("values", []):
        frames = value.get("stacktrace", {}).get("frames", [])
        for f in frames:
            for k in list(f):
                if k not in ("filename", "function", "module", "lineno", "in_app"):
                    f.pop(k)
    e["timestamp"] = FIXED_TIME
    for crumb in e.get("breadcrumbs", {}).get("values", []):
        crumb["timestamp"] = FIXED_TIME
        if "asctime" in crumb.get("data", {}):
            crumb["data"]["asctime"] = FIXED_ASCTIME
    return e


cases = []
for k, raw in enumerate(envelopes):
    head, item, payload = raw.split("\n", 2)
    head = json.loads(head)
    payload = json.loads(payload.rstrip("\n"))
    head["event_id"] = EVENT_IDS[k]
    head["sent_at"] = FIXED_SENT_AT
    head["trace"]["trace_id"] = TRACE_ID
    payload["event_id"] = EVENT_IDS[k]
    payload = reduce_event(payload)
    body = json.dumps(payload, separators=(",", ":"), allow_nan=False)
    item = json.loads(item)
    item["length"] = len(body.encode())
    text = json.dumps(head, separators=(",", ":")) + "\n" + json.dumps(item, separators=(",", ":")) + "\n" + body + "\n"
    cases.append({"name": f"envelope_{k}", "envelope": text})
    print(f"envelope_{k}", len(text), body[:200])

# Python's logging, both of the backend's formats, on records with a
# fixed time and UTC — `asctime` is `%Y-%m-%d %H:%M:%S,mmm`.
logging.Formatter.converter = time.gmtime
plain = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
as_json = logging.Formatter('{"time":"%(asctime)s","name":"%(name)s","level":"%(levelname)s","message":"%(message)s"}')
records = [
    ("info_args", "app.services.email", logging.INFO, "[SES] Email sent to %s, MessageId: %s", ("a@b.co", "0100019")),
    ("warning_int", "app.tasks.extraction_tasks", logging.WARNING, "retrying job %d after %s", (7, "timeout")),
    ("error_plain", "app.api.routers.job_applicant.auth", logging.ERROR, "Registration error", ()),
    ("debug_percent", "app", logging.DEBUG, "progress 100%% of %d", (3,)),
    ("critical_unicode", "app.core", logging.CRITICAL, "café → %s", ("déjà",)),
    ("info_quote", "app", logging.INFO, 'name was "Ada" \\ done', ()),
    ("info_newline", "app", logging.INFO, "line one\nline two", ()),
]
lines = []
for name, logger_name, level, msg, args in records:
    r = logging.LogRecord(logger_name, level, "app/x.py", 1, msg, args, None)
    r.created = 1790065800.123456
    r.msecs = 123.0
    lines.append({"name": name, "logger": logger_name, "level": level, "template": msg, "args": json.dumps(list(args), separators=(",", ":")), "message": r.getMessage(), "plain": plain.format(r), "json": as_json.format(r)})
    print(name, "|", plain.format(r), "|", as_json.format(r))

json.dump({"envelopes": cases, "lines": lines, "auth": sentry_sdk.get_client().transport.parsed_dsn.to_auth("sentry.python/2.69.2").to_header()}, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "sentry_oracle.json"), "w"), indent=1)
print("wrote", len(cases), "envelopes and", len(lines), "lines")
