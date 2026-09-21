"""Capture what SQLAlchemy compiles, as the oracle for the `sql` port.

Run with the backend's interpreter, so the versions are the ones the
backend ships:

    ../shallowflaws/.venv/bin/python examples/sql_oracle.py

It writes src/sql/fixture.tuo: for every case the statement text the
asyncpg dialect sends to PostgreSQL and the positional parameters, in
order, as the text the wire carries. `sql::demo` builds the same
statements with the tuonelang builder and a spec compares them byte for
byte, so the case order here and in `sql::demo::build` must agree.
"""

import datetime
import decimal
import io
import os
import sys
import tempfile

import alembic
import sqlalchemy
from alembic import command
from alembic.config import Config
from sqlalchemy import (JSON, BigInteger, Boolean, Column, Date, DateTime, Numeric, Uuid, Float, ForeignKey, Index, Integer,
                        MetaData, String, Table, Text, and_, delete, exists, func, insert, not_,
                        or_, select, text, update)
from sqlalchemy.dialects.postgresql import asyncpg
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.schema import CreateIndex, CreateTable, DropIndex, DropTable

dialect = asyncpg.dialect()
meta = MetaData()
users = Table(
    "users", meta,
    Column("id", Integer, primary_key=True),
    Column("email", String(255), nullable=False, unique=True),
    Column("name", String),
    Column("age", Integer),
    Column("active", Boolean, nullable=False, server_default=text("true")),
    Column("score", Float),
    Column("balance", BigInteger),
    Column("bio", Text),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
)
jobs = Table(
    "jobs", meta,
    Column("id", Integer, primary_key=True),
    Column("owner_id", Integer, ForeignKey("users.id"), nullable=False),
    Column("title", String(100), nullable=False),
    Column("city", String),
    Column("salary", Integer),
    Column("published", Boolean),
    Column("posted_at", DateTime(timezone=True)),
)
events = Table(
    "events", meta,
    Column("id", Uuid, primary_key=True),
    Column("day", Date, nullable=False),
    Column("at", DateTime),
    Column("amount", Numeric(10, 2)),
    Column("payload", JSON),
)
profiles = Table(
    "profiles", meta,
    Column("id", BigInteger, primary_key=True),
    Column("handle", String(40), unique=True),
    Column("user_id", Integer, ForeignKey("users.id"), nullable=False, unique=True),
    Column("job_id", Integer, ForeignKey("jobs.id")),
    Column("visits", Integer, nullable=False, server_default=text("0")),
)
u, j, e = users.c, jobs.c, events.c
when = datetime.datetime(2026, 9, 21, 12, 30, 0, tzinfo=datetime.timezone.utc)

CASES = [
    ("select_all", select(users)),
    ("select_by_email", select(users).where(u.email == "a@b.c")),
    ("select_two_columns", select(u.id, u.name).where(u.id == 7)),
    ("compare_ne", select(u.id).where(u.age != 30)),
    ("compare_lt", select(u.id).where(u.age < 30)),
    ("compare_le", select(u.id).where(u.age <= 30)),
    ("compare_gt", select(u.id).where(u.score > 1.5)),
    ("compare_ge", select(u.id).where(u.balance >= 5000000000)),
    ("compare_bool", select(u.id).where(u.active == True)),  # noqa: E712
    ("compare_time", select(j.id).where(j.posted_at > when)),
    ("compare_text", select(u.id).where(u.bio == "x")),
    ("compare_columns", select(u.id).where(u.age > u.balance)),
    ("is_null", select(u.id).where(u.name.is_(None))),
    ("is_not_null", select(u.id).where(u.name.isnot(None))),
    ("is_true", select(u.id).where(u.active.is_(True))),
    ("is_false", select(u.id).where(u.active.is_(False))),
    ("eq_none", select(u.id).where(u.name == None)),  # noqa: E711
    ("ne_none", select(u.id).where(u.name != None)),  # noqa: E711
    ("like", select(u.id).where(u.name.like("A%"))),
    ("ilike", select(u.id).where(u.name.ilike("%x%"))),
    ("in_ints", select(u.id).where(u.id.in_([1, 2, 3]))),
    ("in_texts", select(u.id).where(u.email.in_(["a", "b"]))),
    ("in_empty", select(u.id).where(u.id.in_([]))),
    ("not_in", select(u.id).where(u.id.not_in([4, 5]))),
    ("between", select(u.id).where(u.age.between(18, 65))),
    ("and_two", select(u.id).where(and_(u.age >= 18, u.active == True))),  # noqa: E712
    ("where_twice", select(u.id).where(u.age >= 18).where(u.name == "n")),
    ("or_two", select(u.id).where(or_(u.age < 18, u.age > 65))),
    ("or_inside_and", select(u.id).where(and_(u.active.is_(True), or_(u.name.ilike("%a%"), u.bio.ilike("%a%"))))),
    ("and_inside_or", select(u.id).where(or_(and_(u.age > 1, u.age < 5), u.name.is_(None)))),
    ("and_inside_and", select(u.id).where(and_(and_(u.age > 1, u.age < 5), u.id == 2))),
    ("and_of_one", select(u.id).where(and_(u.age > 1))),
    ("not_column", select(u.id).where(not_(u.active))),
    ("not_eq", select(u.id).where(not_(u.age == 3))),
    ("not_lt", select(u.id).where(not_(u.age < 3))),
    ("not_like", select(u.id).where(not_(u.name.like("A%")))),
    ("not_ilike", select(u.id).where(not_(u.name.ilike("A%")))),
    ("not_is_null", select(u.id).where(not_(u.name.is_(None)))),
    ("not_between", select(u.id).where(not_(u.age.between(1, 2)))),
    ("not_in_list", select(u.id).where(not_(u.id.in_([1, 2])))),
    ("not_and", select(u.id).where(not_(and_(u.age > 1, u.age < 5)))),
    ("not_or", select(u.id).where(not_(or_(u.age > 1, u.age < 5)))),
    ("not_inside_and", select(u.id).where(and_(not_(u.active), u.age > 1))),
    ("count_star", select(func.count()).select_from(users)),
    ("count_column", select(func.count(u.id)).where(u.active.is_(True))),
    ("count_labelled", select(func.count(u.id).label("n")).where(u.age > 1)),
    ("sum_coalesce", select(func.coalesce(func.sum(u.age), 0)).where(u.active.is_(True))),
    ("two_coalesce", select(func.coalesce(func.sum(u.age), 0), func.coalesce(func.max(u.score), 1.5))),
    ("coalesce_labelled", select(func.coalesce(func.sum(j.salary), 0).label("total")).where(j.owner_id == 4)),
    ("min_max_avg", select(func.min(u.age), func.max(u.age), func.avg(u.score))),
    ("lower_compare", select(u.id).where(func.lower(u.email) == "a@b.c")),
    ("column_labelled", select(u.name.label("who"), u.id)),
    ("condition_labelled", select(u.name.isnot(None).label("named"))),
    ("order_asc", select(u.id).order_by(u.name)),
    ("order_asc_explicit", select(u.id).order_by(u.name.asc())),
    ("order_desc", select(u.id).order_by(u.created_at.desc())),
    ("order_two", select(u.id).order_by(u.name.asc(), u.id.desc())),
    ("order_nulls_last", select(u.id).order_by(u.score.desc().nulls_last())),
    ("limit", select(u.id).limit(10)),
    ("limit_offset", select(u.id).where(u.age > 1).order_by(u.id.desc()).limit(10).offset(20)),
    ("offset_only", select(u.id).offset(20)),
    ("distinct", select(u.name).distinct()),
    ("group_by", select(j.owner_id, func.count(j.id)).group_by(j.owner_id)),
    ("group_having", select(j.owner_id, func.count(j.id).label("n")).group_by(j.owner_id).having(func.count(j.id) > 2).order_by(func.count(j.id).desc())),
    ("join", select(u.name, j.title).join(jobs, j.owner_id == u.id).where(j.published.is_(True))),
    ("join_from", select(u.name, j.title).select_from(users).join(jobs, j.owner_id == u.id)),
    ("outer_join", select(u.name, func.count(j.id).label("jobs")).outerjoin(jobs, j.owner_id == u.id).group_by(u.name)),
    ("join_and", select(u.id).join(jobs, and_(j.owner_id == u.id, j.published.is_(True)))),
    ("exists", select(u.id).where(exists().where(j.owner_id == u.id))),
    ("exists_select", select(exists().where(u.email == "x"))),
    ("not_exists", select(u.id).where(~exists().where(j.owner_id == u.id))),
    ("in_subquery", select(u.id).where(u.id.in_(select(j.owner_id).where(j.salary > 100)))),
    ("scalar_subquery", select(u.name, select(func.count(j.id)).where(j.owner_id == u.id).scalar_subquery().label("jobs"))),
    ("for_update", select(u.id).where(u.id == 1).with_for_update()),
    ("everything", select(u.id, u.email).where(and_(u.active.is_(True), or_(u.name.ilike("%k%"), u.email.ilike("%k%")), u.age.between(18, 65), u.id.in_([1, 2, 3]))).order_by(u.created_at.desc(), u.id).limit(25).offset(50)),
    ("in_empty_inside_or", select(u.id).where(or_(u.id.in_([]), u.age > 1))),
    ("not_in_empty", select(u.id).where(u.id.not_in([]))),
    ("not_not_column", select(u.id).where(not_(not_(u.active)))),
    ("not_not_in", select(u.id).where(not_(u.id.not_in([1])))),
    ("not_in_inside_and", select(u.id).where(and_(u.id.not_in([1]), u.age > 1))),
    ("arithmetic", select(u.id).where(u.age + 1 > 5)),
    ("arithmetic_minus", select(u.id, (u.balance - u.age).label("rest")).where(u.age * 2 < 9)),
    ("other_kinds", select(e.id).where(and_(e.id == "6f1b0c1e-0d5a-4c0e-9a57-1f2e3d4c5b6a", e.day >= datetime.date(2026, 9, 21), e.at < when.replace(tzinfo=None), e.amount > decimal.Decimal("10.50")))),
    ("int_against_float", select(u.id).where(u.score > 1)),
    ("int_against_numeric", select(e.id).where(e.amount > 10)),
    ("insert_with_key", insert(users).values(id=5, email="k")),
    ("insert_other_kinds", insert(events).values(id="6f1b0c1e-0d5a-4c0e-9a57-1f2e3d4c5b6a", day=datetime.date(2026, 9, 21), amount=12, payload=None)),
    ("insert_values", insert(users).values(email="a@b.c", name="A")),
    ("insert_returning", insert(users).values(email="a@b.c", name="A", age=33).returning(u.id)),
    ("insert_null", insert(users).values(email="a@b.c", name=None)),
    ("insert_every_kind", insert(users).values(email="e", name="n", age=1, active=False, score=2.5, balance=5000000000, bio="b", created_at=when).returning(u.id, u.created_at)),
    ("insert_now", insert(jobs).values(owner_id=1, title="t", posted_at=func.now())),
    ("insert_conflict_nothing", pg_insert(users).values(email="a", name="b").on_conflict_do_nothing(index_elements=["email"])),
    ("insert_conflict_update", pg_insert(users).values(email="a", name="b").on_conflict_do_update(index_elements=["email"], set_={"name": "b"}).returning(u.id)),
    ("update_values", update(users).where(u.id == 3).values(name="z")),
    ("update_null", update(users).where(u.id == 3).values(name="z", age=None)),
    ("update_returning", update(users).where(u.id == 3).values(active=False).returning(u.id, u.active)),
    ("update_now", update(jobs).where(and_(j.owner_id == 3, j.published.is_(False))).values(published=True, posted_at=func.now())),
    ("update_coalesce", update(jobs).where(j.id == 1).values(posted_at=func.coalesce(j.posted_at, when))),
    ("update_increment", update(users).where(u.id == 3).values(age=u.age + 1)),
    ("update_all", update(users).values(active=True)),
    ("delete_where", delete(users).where(u.id == 3)),
    ("delete_and", delete(jobs).where(j.owner_id == 3, j.published.is_(False))),
    ("delete_returning", delete(jobs).where(j.salary < 10).returning(j.id)),
    ("delete_all", delete(jobs)),
]

DDL = [
    ("create_users", CreateTable(users)),
    ("create_jobs", CreateTable(jobs)),
    ("create_events", CreateTable(events)),
    ("create_profiles", CreateTable(profiles)),
    ("create_index", CreateIndex(Index("ix_jobs_city", j.city))),
    ("create_unique_index", CreateIndex(Index("ix_jobs_owner_title", j.owner_id, j.title, unique=True))),
    ("drop_index", DropIndex(Index("ix_jobs_city", j.city))),
    ("drop_jobs", DropTable(jobs)),
]


def wire(value):
    if value is None:
        return None
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    return str(value)


def compiled(statement):
    c = statement.compile(dialect=dialect, compile_kwargs={"render_postcompile": True})
    params = [wire(c.params[name]) for name in c.positiontup]
    return str(c), params


REVISIONS = [
    ("1a2b3c4d5e6f", None, "create users", "CREATE TABLE notes (id SERIAL NOT NULL, PRIMARY KEY (id))", "DROP TABLE notes"),
    ("2b3c4d5e6f70", "1a2b3c4d5e6f", "add body", "ALTER TABLE notes ADD COLUMN body VARCHAR", "ALTER TABLE notes DROP COLUMN body"),
    ("3c4d5e6f7081", "2b3c4d5e6f70", "index body", "CREATE INDEX ix_notes_body ON notes (body)", "DROP INDEX ix_notes_body"),
]


def alembic_sql(action, target):
    with tempfile.TemporaryDirectory() as root:
        os.makedirs(os.path.join(root, "versions"))
        with open(os.path.join(root, "env.py"), "w") as f:
            f.write(
                "from alembic import context\n"
                "context.configure(url='postgresql://', literal_binds=True)\n"
                "with context.begin_transaction():\n"
                "    context.run_migrations()\n"
            )
        with open(os.path.join(root, "script.py.mako"), "w") as f:
            f.write("")
        for rev, down, message, up_sql, down_sql in REVISIONS:
            with open(os.path.join(root, "versions", rev + ".py"), "w") as f:
                f.write(
                    f'"""{message}"""\nfrom alembic import op\n'
                    f"revision = {rev!r}\ndown_revision = {down!r}\n"
                    f"branch_labels = None\ndepends_on = None\n"
                    f"def upgrade():\n    op.execute({up_sql!r})\n"
                    f"def downgrade():\n    op.execute({down_sql!r})\n"
                )
        out = io.StringIO()
        config = Config(output_buffer=out, stdout=io.StringIO())
        config.set_main_option("script_location", root)
        action(config, target, sql=True)
        return out.getvalue()


MIGRATIONS = [
    ("upgrade_head", lambda: alembic_sql(command.upgrade, "head")),
    ("upgrade_partial", lambda: alembic_sql(command.upgrade, "1a2b3c4d5e6f:3c4d5e6f7081")),
    ("downgrade_one", lambda: alembic_sql(command.downgrade, "3c4d5e6f7081:2b3c4d5e6f70")),
    ("downgrade_base", lambda: alembic_sql(command.downgrade, "3c4d5e6f7081:base")),
]


def literal(s):
    out = []
    for ch in s:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def chain(name, doc, ret, rows, fallback):
    lines = [f"/// {doc}", f"pub fn {name}(take i: Int) -> {ret} {{"]
    for k, row in enumerate(rows):
        lines += [f"    if i == {k} {{", f"        return {row};", "    }"]
    lines += [f"    {fallback}", "}", ""]
    return lines


def main():
    rows = []
    for name, statement in CASES:
        sql, params = compiled(statement)
        rows.append((name, sql, params))
    for name, ddl in DDL:
        rows.append((name, str(ddl.compile(dialect=dialect)), []))
    for name, produce in MIGRATIONS:
        rows.append((name, produce(), []))

    if "--print" in sys.argv:
        for name, sql, params in rows:
            print(name, "|", literal(sql), "|", params)
        return

    lines = [
        "// Generated by examples/sql_oracle.py — do not edit by hand.",
        "//",
        f"// SQLAlchemy {sqlalchemy.__version__} compiling for the asyncpg dialect, and Alembic",
        f"// {alembic.__version__} in offline mode, under CPython {sys.version_info.major}.{sys.version_info.minor}: the statement text",
        "// and the positional parameters each case sends to PostgreSQL. A",
        "// parameter list is one string, every value followed by 0x1F, and a NULL",
        "// spelled as the single byte 0x00.",
        "",
        "module sql::fixture;",
        "",
        "/// How many cases were captured.",
        "pub fn count() -> Int {",
        f"    {len(rows)}",
        "}",
        "",
    ]
    lines += chain("name", "The case's name.", "Str", [literal(r[0]) for r in rows], '""')
    lines += chain("statement", "The statement text SQLAlchemy compiled.", "Str", [literal(r[1]) for r in rows], '""')
    packed = []
    for r in rows:
        packed.append(literal("".join(("\x00" if p is None else p) + "\x1f" for p in r[2])).replace("\x1f", "\\u{1f}").replace("\x00", "\\u{0}"))
    lines += chain("parameters", "The positional parameters, packed.", "Str", packed, '""')
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "..", "src", "sql", "fixture.tuo"), "w") as f:
        f.write("\n".join(lines))
    print(f"wrote {len(rows)} cases")


main()
