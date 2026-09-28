"""Capture what python-dotenv and pydantic-settings do, as the oracle for
the `dotenv` and `settings` ports.

    ../shallowflaws/.venv/bin/python examples/settings_oracle.py

Two halves. The first hands python-dotenv 1.2.3 a corpus of `.env` texts
— quoting, escapes, `export`, comments, CR and CRLF line ends, a BOM,
broken statements, `${VAR:-default}` expansion against a fixed
environment — plus the backend's three tracked `.env.*.example` files,
and records `dotenv_values` (as `[key, value]` pairs, in order) and the
lines python-dotenv warns it could not parse.

The second instantiates the backend's own `Settings` (app/config.py,
pydantic-settings 2.15.0 on Pydantic 2.13.5) with the process
environment replaced wholesale for each case and, when the case has one,
a `.env` file, and records either `model_dump(mode="json")` or the
validation errors — as FastAPI would render them, `{"detail": [...]}`
without URLs — or the `SettingsError` message. The declaration of every
field (name, type, default, validation aliases) is read from
`Settings.model_fields` and recorded too, so the port binds the same
declaration without the application being written out by hand.
"""

import io
import json
import os
import sys
import tempfile

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "shallowflaws"))
sys.path.insert(0, BACKEND)

# app.config builds a Settings at import time from the working directory's
# .env; import it from an empty directory with a clean environment so the
# developer's own .env (which holds secrets) is never read.
scratch = tempfile.mkdtemp()
os.chdir(scratch)
saved = dict(os.environ)
os.environ.clear()

import typing  # noqa: E402

from dotenv import dotenv_values  # noqa: E402
from dotenv.parser import parse_stream  # noqa: E402
from pydantic import AliasChoices, ValidationError  # noqa: E402
from pydantic_settings import SettingsError  # noqa: E402

from app.config import Settings  # noqa: E402


def dumps(value):
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def with_environ(env, fn):
    os.environ.clear()
    os.environ.update(env)
    try:
        return fn()
    finally:
        os.environ.clear()


# ---------------------------------------------------------------------------
# python-dotenv
# ---------------------------------------------------------------------------

EXPAND_ENV = {"HOME_X": "/home/x", "A": "from-env", "EMPTY": "", "lower": "l"}

DOTENV_CASES = [
    ("basic", "A=1\nB=two words\nC=trailing spaces   \n"),
    ("export", "export A=1\nexport   B=2\nexportC=3\nexport\tD=4\nexport =5\n"),
    ("comments", "# a comment\nA=1 # trailing\nB=1#not-a-comment\nC='q' # after quote\nD=\"q\"# tight\n  # indented\nE=x\t# tab\n"),
    ("single_quoted", "A='a\\'b'\nB='x\\\\y'\nC='\\n stays'\nD='#not comment'\nE=''\nF='  spaced  '\n"),
    ("double_quoted", "A=\"a\\nb\\tc\\\\d\\\"e\\'f\"\nB=\"\\a\\b\\f\\v\\r\\x41\\u00e9\"\nC=\"# kept\"\nD=\"\"\nE=\"  spaced  \"\n"),
    ("multiline", "A=\"line1\nline2\"\nB='x\ny'\nC=\"a\r\nb\"\nD=after\n"),
    ("no_value", "A\nB=\nC= \nD=\"\"\nE # comment\nF=\t\n"),
    ("whitespace", "  A = 1  \n\tB\t=\t2\t\n\n\n   \nC=3\n"),
    ("line_ends", "A=1\r\nB=2\rC=3\r\nD=\"x\"\rE=4"),
    ("bom", "﻿A=1\nB=2\n"),
    ("broken", "A=\"unterminated\nB=2\n=nokey\nC=3\n'bad key\nD=4\nE=\"x\" junk\nF=5\n"),
    ("broken_tail", "A=1\nB='open"),
    ("expand", "A=${HOME_X}\nB=${MISSING:-def}\nC=${A}-${B}\nD='${A}'\nE=\"${A}\"\nF=$A\nG=${}\nH=${A:-}\nI=${UNSET}\nJ=${EMPTY:-fallback}\nK=${lower}\nL=${A:-x}${A}\nM=${A\nN=${A:-a:b}\nO=${A:=x}\n"),
    ("expand_order", "X=${Y}\nY=y\nZ=${Y}\nY=again\nW=${Y}\n"),
    ("expand_none", "NOVAL\nA=${NOVAL}-${NOVAL:-d}\n"),
    ("keys", "'quoted key'=1\nkey.with.dots=2\nKEY-DASH=3\nü=4\n'q'x=5\nlower=6\nMiXeD=7\n"),
    ("unicode_space", "A=x # comment\nB=y　\nC=z #c\nD=été\n"),
    ("duplicates", "A=1\nB=2\nA=3\n"),
    ("quote_then_comment", "A='x'#c\nB=\"y\"   # c\nC='z' junk # c\n"),
    ("equals_in_value", "URL=postgres://u:p@h:5432/db?sslmode=require&x=a=b\nB==x\n"),
    ("json_values", "CORS_ORIGINS=[\"https://a.example\", \"https://b.example\"]\nQ='[1, 2]'\n"),
    ("leading_hash", "A=#x\nB= #y\nC=  # c\nD=a #b #c\nE=a\t#b\n"),
    ("blank_then_broken", "A=1\n\n\n  B='x\nC=2\n"),
    ("broken_crlf", "A='x\r\nB=1\r\nC='y\r\nD=2\r\n"),
    ("empty", ""),
    ("only_comments", "# nothing\n\n# here\n"),
]

EXAMPLE_FILES = [".env.example", ".env.analysis.example", ".env.crawler.example"]

dotenv_cases = []


def capture_dotenv(name, text):
    values = with_environ(EXPAND_ENV, lambda: dotenv_values(stream=io.StringIO(text)))
    broken = [b.original.line for b in parse_stream(io.StringIO(text)) if b.error]
    dotenv_cases.append({"name": name, "text": text, "values": [[k, v] for k, v in values.items()], "errors": broken})
    print("dotenv", name, len(values), "values", len(broken), "errors")


for name, text in DOTENV_CASES:
    capture_dotenv(name, text)
for f in EXAMPLE_FILES:
    capture_dotenv("file" + f.replace(".", "_"), open(os.path.join(BACKEND, f), encoding="utf-8").read())

# ---------------------------------------------------------------------------
# pydantic-settings
# ---------------------------------------------------------------------------


def kind_of(annotation):
    optional = False
    if typing.get_origin(annotation) is typing.Union:
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        assert len(args) == 1, annotation
        annotation, optional = args[0], True
    if annotation is str:
        return "str", optional
    if annotation is int:
        return "int", optional
    if annotation is float:
        return "float", optional
    if annotation is bool:
        return "bool", optional
    if annotation == list[str]:
        return "list_str", optional
    if annotation == list[int]:
        return "list_int", optional
    raise SystemExit(f"unexpected annotation {annotation!r}")


fields = []
for name, info in Settings.model_fields.items():
    kind, optional = kind_of(info.annotation)
    alias = info.validation_alias
    if alias is None:
        aliases = []
    elif isinstance(alias, str):
        aliases = [alias]
    elif isinstance(alias, AliasChoices):
        aliases = list(alias.choices)
    else:
        raise SystemExit(f"unexpected alias {alias!r}")
    fields.append({"name": name, "kind": kind, "optional": optional, "default": dumps(info.default), "aliases": aliases})

assert Settings.model_config.get("extra") == "forbid"
assert Settings.model_config.get("case_sensitive") is False


def example(f):
    return open(os.path.join(BACKEND, f), encoding="utf-8").read()


SETTINGS_CASES = [
    ("defaults", {}, None),
    ("env_scalars", {"DEBUG": "false", "SMTP_PORT": "2525", "WEIGHT_COMMERCIAL": "0.65", "ENVIRONMENT": "production", "API_TITLE": "Ré \"q\" <api>\n"}, None),
    ("env_lax", {"debug": "0", "Smtp_Port": " 2_525 ", "environment": "staging", "VIES_TEST_MODE": "YES", "SMTP_USE_TLS": "on", "VLLM_STARTUP_TIMEOUT": "600.0", "CRAWLER_REQUEST_TIMEOUT": " 12 ", "LLM_USD_TO_EUR": "1e0", "WEIGHT_PRIVATE": ".25", "REDIS_DB": "-1"}, None),
    ("env_bool_spellings", {"DEBUG": "t", "SMTP_USE_TLS": "n", "SMTP_USE_SSL": "True", "VIES_TEST_MODE": "OFF", "ENABLE_DEEP_ANALYSIS": "y", "PROXY_FALLBACK_ON_ERROR": "f", "USAGE_BASED_BILLING": "1"}, None),
    ("alias_choice_second", {"JWT_SECRET": "jwt", "DB_URL": "postgresql://db", "CACHE_URL": "redis://cache"}, None),
    ("alias_choice_both", {"JWT_SECRET": "jwt", "SECRET_KEY": "sk", "DB_URL": "postgresql://second", "DATABASE_URL": "postgresql://first", "REDIS_URL": "redis://first", "CACHE_URL": "redis://second"}, None),
    ("alias_hides_name", {"R2_ENDPOINT_OVERRIDE": "http://ignored", "R2_ACCOUNT_ID": "acct", "r2_bucket": "b"}, None),
    ("alias_used", {"R2_ENDPOINT_URL": "http://localhost:9000", "SENTRY_DSN": "https://k@o1.ingest.sentry.io/2"}, None),
    ("optional_empty", {"SMTP_USER": "", "SMTP_PASSWORD": " ", "ENVIRONMENT": ""}, None),
    ("lists", {"CORS_ORIGINS": "[\"https://a.example\",\"https://b.example\"]", "TRUSTED_HOSTS": " [ ] ", "PROXY_FALLBACK_STATUSES": "[403, \"429\", 500.0, true]"}, None),
    ("list_not_json", {"CORS_ORIGINS": "https://a.example"}, None),
    ("list_empty_string", {"TRUSTED_HOSTS": ""}, None),
    ("list_items_wrong", {"CORS_ORIGINS": "[1, \"ok\", null]", "PROXY_FALLBACK_STATUSES": "[1, \"x\", 2.5, null]"}, None),
    ("list_not_list", {"CORS_ORIGINS": "{\"a\":1}", "TRUSTED_HOSTS": "\"x\"", "PROXY_FALLBACK_STATUSES": "403"}, None),
    ("scalar_errors", {"DEBUG": "maybe", "SMTP_PORT": "25.5", "REDIS_PORT": "abc", "DATABASE_PORT": "", "WEIGHT_COMMERCIAL": "abc", "VLLM_PORT": "1e3", "MAX_FILE_SIZE_MB": "0x10"}, None),
    ("dotenv_basic", {}, "ENVIRONMENT=production\nDEBUG=false\nexport SMTP_PORT=2525\nFRONTEND_URL='https://rekryon.example'\n"),
    ("dotenv_env_wins", {"ENVIRONMENT": "from-env", "SMTP_PORT": "1"}, "ENVIRONMENT=from-file\nSMTP_PORT=2\nSMTP_HOST=file-host\n"),
    ("dotenv_case", {}, "debug=false\nSmtp_Host=mixed\nsecret_key=lower-alias\n"),
    ("dotenv_alias_second", {}, "JWT_SECRET=from-file\nDB_URL=postgresql://file\n"),
    ("dotenv_alias_across_sources", {"JWT_SECRET": "env-second"}, "SECRET_KEY=file-first\n"),
    ("dotenv_extras", {}, "ENVIRONMENT=x\nCELERY_QUEUES=a,b\nEMPTY_EXTRA=\nNO_VALUE\nCelery_Hostname=w@%h\n"),
    ("dotenv_field_name_behind_alias", {}, "R2_ENDPOINT_OVERRIDE=http://localhost:9000\n"),
    ("dotenv_expand", {"HOST_NAME": "rekryon.example"}, "FRONTEND_URL=https://${HOST_NAME}/app\nAPI_TITLE=${API_TITLE:-Fallback}\nSMTP_HOST=${FRONTEND_URL}\n"),
    ("dotenv_lists", {}, "CORS_ORIGINS=[\"https://a.example\"]\nPROXY_FALLBACK_STATUSES='[429]'\n"),
    ("dotenv_list_empty", {}, "CORS_ORIGINS=\n"),
    ("dotenv_list_broken_env_ok", {"CORS_ORIGINS": "[]"}, "CORS_ORIGINS=nope\n"),
    ("env_list_broken_before_dotenv", {"CORS_ORIGINS": "nope"}, "TRUSTED_HOSTS=nope\n"),
    ("dotenv_errors", {"DEBUG": "yes"}, "DEBUG=maybe\nSMTP_PORT=abc\nEXTRA_ONE=1\n"),
    ("dotenv_error_and_extra", {}, "SMTP_PORT=abc\nEXTRA_ONE=1\nDEBUG=nope\n"),
    ("example_cloud", {}, example(".env.example")),
    ("example_analysis", {}, example(".env.analysis.example")),
    ("example_crawler", {}, example(".env.crawler.example")),
    ("example_cloud_fixed", {"CELERY_QUEUES": "q"}, "\n".join(l for l in example(".env.example").splitlines() if not l.startswith("CELERY_")) + "\n"),
]

settings_cases = []
for name, env, text in SETTINGS_CASES:
    path = None
    if text is not None:
        path = os.path.join(scratch, name + ".env")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def build():
        return Settings(_env_file=path)

    record = {"name": name, "env": [[k, v] for k, v in env.items()], "dotenv": text, "outcome": "", "output": ""}
    try:
        s = with_environ(env, build)
        record["outcome"] = "ok"
        record["output"] = dumps(s.model_dump(mode="json"))
    except ValidationError as e:
        record["outcome"] = "invalid"
        record["output"] = dumps({"detail": e.errors(include_url=False)})
    except SettingsError as e:
        record["outcome"] = "error"
        record["output"] = str(e)
    settings_cases.append(record)
    print("settings", name, record["outcome"])

os.environ.update(saved)
here = os.path.dirname(os.path.abspath(__file__))
json.dump({"expand_env": [[k, v] for k, v in EXPAND_ENV.items()], "dotenv": dotenv_cases, "fields": fields, "settings": settings_cases},
          open(os.path.join(here, "settings_oracle.json"), "w"), indent=1, ensure_ascii=False)
print("wrote", len(dotenv_cases), "dotenv cases,", len(fields), "fields,", len(settings_cases), "settings cases")
