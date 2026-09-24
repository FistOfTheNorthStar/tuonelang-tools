"""Capture what Jinja2 renders, as the oracle for the `jinja` port.

    ../shallowflaws/.venv/bin/python examples/jinja_oracle.py

Calls every email renderer in app/emails — the backend's own functions,
its templates, its translations — in English and Finnish, with values
that carry markup, quotes, and non-ASCII text, and records for each
render: the template's name, the context Jinja2 was given (as JSON, with
the paths whose values were `Markup` listed, since a safe string is not
escaped), and the output. The template sources go in too, so the port
renders exactly the files the backend ships. Jinja2 3.1.6, MarkupSafe
3.0.3, with the environment app/emails/base.py builds: autoescape for
`.html`, `trim_blocks`, `lstrip_blocks`, and the `currency` filter.
"""

import json
import os
import re
import sys

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "shallowflaws"))
os.chdir(BACKEND)
sys.path.insert(0, BACKEND)
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x:y@127.0.0.1:1/z")
os.environ["FRONTEND_URL"] = "https://rekryon.example"
os.environ["EMAIL_COMPANY_NAME"] = "Rekryon <Oy> & Co"

import jinja2  # noqa: E402
from markupsafe import Markup  # noqa: E402

from app.emails import analysis, auth, conversations, extraction, payments, vies  # noqa: E402
from app.emails.base import TEMPLATES_DIR  # noqa: E402

renders = []
original = jinja2.Template.render


def recording(self, *args, **kwargs):
    out = original(self, *args, **kwargs)
    renders.append((self.name, dict(*args, **kwargs), out))
    return out


jinja2.Template.render = recording


def convert(value, path, safe):
    if isinstance(value, Markup):
        safe.append(path)
        return str(value)
    if isinstance(value, dict):
        return {k: convert(v, f"{path}.{k}" if path else k, safe) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [convert(v, f"{path}.{i}", safe) for i, v in enumerate(value)]
    return value


NAME = 'Ada <b>"Lovelace"</b> & Söhne'
cases = []
for language in ("en", "fi"):
    calls = [
        ("password_reset", lambda: auth.render_password_reset_email("ada@example.com", "tok<en>", "job_applicant", NAME, language)),
        ("password_reset_anonymous", lambda: auth.render_password_reset_email("ada@example.com", "t", "employer", None, language)),
        ("welcome_with_verification", lambda: auth.render_welcome_email(NAME, "job_applicant", language, "ada@example.com", "v&t")),
        ("welcome_plain", lambda: auth.render_welcome_email(None, "employer", language)),
        ("email_verification", lambda: auth.render_email_verification_email("ada@example.com", "v", "employer", NAME, language)),
        ("password_changed", lambda: auth.render_password_changed_email(NAME, language)),
        ("credits_purchase", lambda: payments.render_credits_purchase_success_email(NAME, 250, "49", "EUR", "2027-01-01", language)),
        ("token_purchase", lambda: payments.render_token_purchase_success_email(NAME, 1000, "12.5", "USD", "2027-01-01", language)),
        ("payment_failed", lambda: payments.render_payment_failed_email(NAME, "credits purchase", "nope", "EUR", "card <declined>", language)),
        ("payment_failed_no_reason", lambda: payments.render_payment_failed_email(None, "purchase", "9.99", "EUR", None, language)),
        ("analysis_started_expired", lambda: analysis.render_analysis_started_email(NAME, "Chef <de> cuisine", 7, "expired", language)),
        ("analysis_started_tier", lambda: analysis.render_analysis_started_email(None, "Cook", 8, "tier_reached", language)),
        ("analysis_started_other", lambda: analysis.render_analysis_started_email(None, "Cook", 9, "manual", language)),
        ("analysis_complete_many", lambda: analysis.render_analysis_complete_email(NAME, "Chef", 7, 12, 3, language)),
        ("analysis_complete_one", lambda: analysis.render_analysis_complete_email(NAME, "Chef", 7, 1, 1, language)),
        ("analysis_complete_none", lambda: analysis.render_analysis_complete_email(NAME, "Chef", 7, 0, 0, language)),
        ("unread_message", lambda: conversations.render_unread_message_email(NAME, "Bob & <Co>", "Chef", "See you at 5 > 4?", "https://rekryon.example/c/1?x=1&y=2", language)),
        ("extraction_complete", lambda: extraction.render_extraction_complete_email(NAME, "Q3 <batch>", "abc", 12, 2, language)),
        ("extraction_complete_clean", lambda: extraction.render_extraction_complete_email(None, "Q3", "abc", 1, 0, language)),
        ("extraction_failed", lambda: extraction.render_extraction_failed_email(NAME, "Q3", "abc", "zip <corrupt>", language)),
        ("extraction_failed_silent", lambda: extraction.render_extraction_failed_email(None, "Q3", "abc", "", language)),
        ("vies_success_match", lambda: vies.render_vies_success_email(user_name=NAME, company_name="Ada & Co", vat_country_code="FI", vat_number="12345678", vies_name="ADA & CO OY", name_match_ok=True, name_match_score=0.93, language=language)),
        ("vies_success_mismatch", lambda: vies.render_vies_success_email(user_name=None, company_name=None, vat_country_code="DE", vat_number="1", vies_name=None, name_match_ok=False, name_match_score=0.0, language=language)),
        ("vies_failed", lambda: vies.render_vies_failed_email(user_name=NAME, company_name="Ada & Co", vat_country_code="FI", vat_number="1", error_reason="VIES <down>", language=language)),
        ("vies_failed_silent", lambda: vies.render_vies_failed_email(user_name=None, company_name=None, vat_country_code="FI", vat_number="1", error_reason=None, language=language)),
    ]
    for name, call in calls:
        del renders[:]
        try:
            call()
        except Exception as e:  # noqa: BLE001
            print("skipped", name, language, repr(e))
            continue
        for template_name, context, out in renders:
            safe = []
            # The translations are a whole language; keep the sections this
            # template and its base actually read, so the fixture stays small.
            source = open(os.path.join(TEMPLATES_DIR, template_name), encoding="utf-8").read()
            used = set(re.findall(r"\bt\.([A-Za-z_]+)", source))
            context = dict(context)
            context["t"] = {k: v for k, v in context["t"].items() if k in used}
            cases.append({"name": f"{language}_{name}_{template_name.rsplit('.', 1)[1]}", "template": template_name, "context": convert(context, "", safe), "safe": safe, "output": out})
            print(cases[-1]["name"], len(out))

sources = {}
for root, _, files in os.walk(TEMPLATES_DIR):
    for f in files:
        path = os.path.join(root, f)
        sources[os.path.relpath(path, TEMPLATES_DIR)] = open(path, encoding="utf-8").read()

here = os.path.dirname(os.path.abspath(__file__))
json.dump({"sources": sources, "cases": cases}, open(os.path.join(here, "jinja_oracle.json"), "w"), indent=1, ensure_ascii=False)
print("wrote", len(cases), "renders of", len(sources), "templates")
