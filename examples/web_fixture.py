"""Turn oracle.json (written by web_oracle.py) into src/web/fixture.tuo.

    cd examples
    ../../shallowflaws/.venv/bin/python web_oracle.py    # writes oracle.json
    python3 web_fixture.py                               # rewrites ../src/web/fixture.tuo
    tuo fmt ../src/web/fixture.tuo
"""
import json

cases = json.load(open("oracle.json"))


def lit(s):
    out = []
    for ch in s:
        o = ord(ch)
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\r":
            out.append("\\r")
        elif o < 32 or o > 126:
            out.append("\\u{%x}" % o)
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def table(name, doc, vals, ret="Str", default='""'):
    body = "\n".join(
        f"    if index == {i} {{\n        return {v};\n    }}" for i, v in enumerate(vals)
    )
    return (
        f"/// {doc}\n///\n/// # Example\n/// ```tuo\n/// let v = web::fixture::{name}(0);\n/// ```\n"
        f"pub fn {name}(take index: Int) -> {ret} {{\n{body}\n    {default}\n}}\n"
    )


for c in cases:
    if c["location"]:
        c["location"] = c["location"].replace("http://testserver", "")

header = open("../src/web/fixture.tuo").read().split("module web::fixture;")[0]
src = header + "module web::fixture;\n\n"
src += (
    "/// How many cases there are.\n///\n/// # Example\n/// ```tuo\n"
    f"/// let n = web::fixture::count(); // {len(cases)}\n/// ```\n"
    f"pub fn count() -> Int {{\n    {len(cases)}\n}}\n\n"
)
src += table("name", "The name of case `index`.", [lit(c["name"]) for c in cases]) + "\n"
src += table("method", "The request method of case `index`.", [lit("POST" if c["method"] == "POST_NOCT" else c["method"]) for c in cases]) + "\n"
src += table("target", "The request target of case `index`.", [lit(c["path"]) for c in cases]) + "\n"
src += table("content_type", "The request's Content-Type in case `index`, or empty when it had no body.", [lit(c["content_type"]) for c in cases]) + "\n"
src += table("body", "The request body of case `index`.", [lit(c["body"] or "") for c in cases]) + "\n"
src += table("status", "The status FastAPI answered case `index` with.", [str(c["status"]) for c in cases], "Int", "0") + "\n"
src += table("allow", "The `Allow` header FastAPI sent in case `index`, or empty.", [lit(c["allow"] or "") for c in cases]) + "\n"
src += table("location", "The `Location` FastAPI sent in case `index`, as a path, or empty.", [lit(c["location"] or "") for c in cases]) + "\n"
src += table("response", "The body FastAPI answered case `index` with.", [lit(c["response"]) for c in cases])
open("../src/web/fixture.tuo", "w").write(src)
print(len(cases), "cases")
