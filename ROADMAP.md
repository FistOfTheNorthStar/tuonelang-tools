# tuonelang-python-tools

The **Python dependency surface of `shallowflaws/`**, tracked as a port roadmap
to [tuonelang](../tuonelang).

The goal is not "reimplement PyPI." It is to reach the point where the backend
runs with **no Python runtime** — which means porting exactly the libraries that
the production stack actually imports, in the order that unblocks the most.

The list below is derived from `shallowflaws/Pipfile`, and each entry is graded
against what tuonelang v0 can express today (`std::net` TCP/UDP, `std::crypto`
SHA-256/HMAC/PBKDF2, `std::bignum`, `std::json`, `std::fs`, `std::sync`,
`std::rt::par_map`) and what it still refuses (capturing closures, `Box`/
`Shared`/`Weak`, recursive nominal types, TLS, DNS).

## Status of existing ports

| Repo | Replaces | State |
|---|---|---|
| [`tuonelang-db`](../tuonelang-db) | `psycopg2` / `asyncpg` + a vector store | PostgreSQL v3 wire protocol over raw TCP; 130 specs |
| [`tuolang-celery`](../tuolang-celery) | `celery` + `redis` broker | queue engine, routing, retries, beat; 57 specs |
| this repo, `dns` | `socket.getaddrinfo` | stub resolver over UDP; 82 specs; resolves real names |
| this repo, `argon2` | `passlib[argon2]` | Argon2id/i/d + BLAKE2b + PHC strings; 125 specs; verifies the backend's own hashes |
| this repo, `redis` | `redis` | RESP2 client, the backend's commands, `redis://` URLs; 40 specs; 35 live checks against Redis 7 |
| this repo, `http` | `httpx`, `requests`, `uvicorn` | HTTP/1.1 framing, client with redirects, keep-alive server; 107 specs; 29 live checks |
| this repo, `tls` | `ssl` (client side) | TLS 1.3 client on the catalog's stack; RFC 8448 reproduced; handshakes with `std::tls` and OpenSSL; `https://` to Stripe, Sentry, Cloudflare |
| this repo, `web` | `fastapi`, `pydantic`, `starlette` (the request path) | routing, models as data, lax validation, the 422 body; 107 captured FastAPI responses reproduced byte for byte |
| this repo, `x509` + `ec` + `rsa` | the trust half of `ssl`, `certifi` | X.509 parsing and chain validation, a root store, ECDSA P-256/P-384, RSA v1.5/PSS; 380 specs; real chains validated |

All nine prove the thesis: the *engine* of a Python library is pure logic, and pure
logic is what tuonelang specs pin best.

---

## Tier 1 — the critical path

Nothing else ships without these. Each is a hard blocker for the request path.

### 1. TLS 1.3 — replaces the `ssl` module (blocks `httpx`, `stripe`, `boto3`, `redis` over TLS) — ✅ done

**The single highest-value port, and the one everything external waits on.**
On 2026-09-15 the tuonelang catalog landed X25519, ChaCha20-Poly1305,
Ed25519, HKDF, DER, and a TLS 1.3 *server* verified against OpenSSL. This
repo added the *client* the same day, and on 2026-09-16 the trust layer
public servers need: ECDSA on P-256 and P-384, RSA PKCS#1 v1.5 and PSS,
X.509 parsing with names, dates, and the constraint extensions, a
depth-first chain walk, and 19 roots from the Mozilla bundle.
`http::client::get` of an `https://` URL reaches `api.stripe.com`,
`sentry.io`, and Cloudflare, with every chain validated — and the walk
is spec'd to fail for every reason RFC 5280 names, which is where the
real bugs live. One runtime gap surfaced: there is no wall clock, so the
time comes from an SNTP query (`tls::clock`) until the catalog grows one.

Left for later, each a small increment: `rediss://` in the Redis client
(the session is there; the URL flag is refused today), name constraints,
revocation, Certificate Transparency, AES-GCM and P-256 key exchange for
servers that refuse ChaCha20 or X25519 (none tried do).

### 2. DNS resolution — replaces `socket.getaddrinfo` — ✅ done

Explicitly named in the tuonelang README as belonging *in tuonelang* on the UDP
primitives, which already exist (`std::net::udp_send` / `udp_recv`). This is a
small, self-contained, entirely achievable port: A/AAAA/CNAME record parsing
over UDP, with a wire-format parser that specs cleanly. Done first as a
warm-up — it was the shortest path to a real win, and it surfaced a runtime
bug (`udp_bind` on loopback) that ADR-0017 had to be amended for.

### 3. HTTP/1.1 client + server — replaces `httpx`, `requests`, `uvicorn`, `gunicorn` — ✅ done

`examples/http-service` had already served itself over a live loopback
socket, so the socket half was proven. The port adds the protocol: RFC 9112
body framing (with the smuggling shapes refused), chunked transfer encoding
both ways, keep-alive on the server, header parsing with injection refused
on the way out, redirects with the RFC's method changes, bounded timeouts,
and name resolution through `dns`. Client-side connection pooling is the
one item left; `http::message::message_end` is what it loops on. Pair it
with TLS and the whole outbound-integration surface (Stripe, R2, Sentry)
becomes reachable.

### 4. FastAPI-equivalent routing + validation — replaces `fastapi` + `pydantic` — ◐ the request path is done

`examples/router` exists as a seed. This is the largest port by surface area but
the most natural fit: Pydantic is runtime type validation, and tuonelang has
*compile-time* types. Most of Pydantic's job disappears rather than being
reimplemented — the schema becomes a struct, and validation becomes parsing at
the boundary. Focus on: route matching, path/query extraction, JSON body
decoding into structs, and error responses.

Done on 2026-09-19 as `web`: Starlette's route resolution, request models
as data, Pydantic's lax validation of bodies and of query and path
parameters, and FastAPI's 422 body — pinned against 107 responses
captured from the real FastAPI in the backend's virtualenv, reproduced
byte for byte. Still to come, as the backend's routes are ported: nested
models, enums, dates, headers as parameters, JWT bearer auth (HS256 is
`std::crypto::hmac_sha256` away), CORS, and multipart uploads. Handlers
are dispatched by a `match` on a slot, since a route table cannot hold
function values in v0.

### 5. SQLAlchemy-equivalent query layer — replaces `sqlalchemy` + `alembic` — ✅ done

`tuonelang-db` gives you the wire protocol. What is missing is the layer above:
typed row mapping, a query builder, connection pooling, transactions, and
migrations. Skip the ORM identity map and lazy loading — they need capturing
closures and recursive types, both refused today. Build a typed query builder
instead; it is a better fit for the language and a better tool anyway.

Done on 2026-09-21 as `sql`: a query builder whose text is SQLAlchemy's
own — 117 statements compiled by SQLAlchemy 2.0.54 for asyncpg, and by
Alembic 1.20.0 offline, rebuilt byte for byte — with row access by name,
nested transactions, a pool with pre-ping and reset-on-return, and
Alembic-compatible migrations run in one transaction. It also gave the
vendored `tuonelang-db` adapter the login a stock PostgreSQL demands,
SCRAM-SHA-256, spec'd against RFC 7677. All of it is exercised by 51
live checks against PostgreSQL 18. Still to come: TLS to the database,
aliases and CTEs, PostGIS expressions, and the backend's models written
as `sql::table` definitions with their row mappers.

### 6. Argon2 password hashing — replaces `passlib[argon2]` — ✅ done

`std::crypto::pbkdf2_sha256` already existed, so the KDF pattern was
established. Argon2id needed BLAKE2b and a memory-hard fill — both
expressible on `Array[Int]`, with every 64-bit operation spelled out over
the trapping signed `Int`. Spec'd against RFC 7693, argon2-cffi, and the
PHC string format at the smallest geometry; RFC 9106's own vectors and the
backend's production 64 MiB hash exceed the spec sandbox's instruction fuel
and are asserted natively by `examples/argon2.tuo` instead.

---

## Tier 2 — high value, no blockers

Achievable on v0 as it stands, in rough order of value-per-effort.

| Port | Replaces | Notes |
|---|---|---|
| **Redis client** — ✅ done | `redis` | RESP2 over TCP, spec'd against bytes a real server sent; the transport `tuolang-celery` lacked, which still models the broker in memory until its message envelope is written over this client. |
| **Structured logging + Sentry** — ✅ done | `sentry-sdk` | Needs only JSON + HTTP. Error capture, breadcrumbs, envelope format. Done 2026-09-22 as `log` and `sentry`: pinned to CPython's `logging` output and to 7 envelopes sentry-sdk 2.69 serialized, with the logging integration's breadcrumbs and events. |
| **JWT / JOSE** | (part of your auth) | HMAC-SHA256 already exists — HS256 is nearly free today. RS256 verification is `rsa::verify` now; signing waits on a constant-time bignum. |
| **S3 client** — ✅ done | `boto3` | You only use Cloudflare R2. SigV4 signing is pure HMAC-SHA256 — already available. A tiny, targeted client beats all of boto3. Done 2026-09-22 as `s3`: the seven calls the backend makes, pinned to 22 requests captured from boto3 with a frozen clock, and a 35-check live oracle against MinIO. |
| **Template engine** | `jinja2` + `markupsafe` | Parser + renderer; escaping is a correctness property that specs pin beautifully. |
| **Config / env loading** | `pydantic-settings`, `python-dotenv` | Nearly trivial; `.env` parsing + typed struct binding. |
| **CSV / XLSX reading** | `openpyxl` | XLSX is a zip of XML — needs a deflate decompressor first (worth having anyway). |
| **Rate limiting** | `slowapi` | Pure algorithm: token bucket / sliding window. Ideal spec target. |
| **Test runner assertions** | `pytest` | Largely **already solved** — colocated `spec` blocks *are* the replacement. Only fixtures and parametrization are missing. |
| **Formatter / linter / typechecker** | `black`, `flake8`, `mypy` | **Already solved.** `tuo fmt` is canonical and zero-config; the type and ownership checkers subsume mypy and flake8 entirely. |

---

## Tier 3 — blocked or deliberately out of scope

Be honest about these rather than half-porting them.

| Python lib | Why it is hard |
|---|---|
| `torch`, `torchvision` | Needs BLAS-class linear algebra, and realistically GPU. The `gguf-reader` example shows *inference* is reachable; training is not. Consider a narrow inference-only port for the questionnaire matching, not a torch replacement. |
| `pyllym` (LLM framework) | Mostly HTTP + JSON + prompt templating — becomes easy once Tier 1 lands. Low priority, high tractability. |
| `pillow`, `python-docx`, `reportlab`, `pypdf` | Binary format encoders/decoders. Each is a large, self-contained project. PDF *generation* is the most tractable; image *decoding* is the least. |
| `beautifulsoup4`, `lxml` | An HTML5 tolerant parser is a genuinely large spec, but it is pure parsing — a strong fit for TDG, just expensive. |
| `shapely`, `geoalchemy2` | PostGIS does the spatial work server-side; you mostly need WKB parsing, not a geometry engine. Scope this down aggressively. |
| `greenlet`, `asyncio` | Architecturally moot. `std::rt::par_map` plus real threads and channels replaces the async model rather than porting it. |
| `stripe` | Thin HTTP+JSON wrapper. Free once Tier 1 lands; do not port ahead of it. |

---

## Recommended order

1. ✅ **DNS** — small, unblocks name resolution, proves the UDP primitives.
2. ✅ **Argon2** — small, self-contained, RFC test vectors, real security value.
3. ✅ **Redis client** — the transport `tuolang-celery` needs to talk to a real broker.
4. ✅ **HTTP/1.1** — the backbone of everything outbound and inbound.
5. ✅ **TLS 1.3** — the client, and the chain validation that makes it
   reach Stripe, R2, and Sentry.
6. ◐ **Router + validation** — the request path is done — and ✅ the
   **query layer**. Tier 1 is complete.
7. ✅ **S3 client** — the first of Tier 2.
8. ✅ **Structured logging + Sentry**. **Next:** JWT/JOSE, then the
   template engine, then config loading.

The application itself — shallowflaws's routers, services, and models —
stays in Python and is **out of scope** here. It is the oracle these
ports are pinned against, never the thing being ported.

Tiers 2 and 3 fall out largely for free once 4 and 5 exist.

## The migration tool

[`tools/py2tuo`](../tuonelang/tools/py2tuo) compiles a typed subset of Python to
tuonelang and refuses the rest with a positioned diagnostic. It is not a path to
porting these libraries — the semantic gap (exceptions, classes, closures,
arbitrary-precision ints) is too wide — but it is useful for mechanically
translating **pure leaf functions** during a port, with CPython as the
differential oracle.
