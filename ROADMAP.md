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

All four prove the thesis: the *engine* of a Python library is pure logic, and pure
logic is what tuonelang specs pin best.

---

## Tier 1 — the critical path

Nothing else ships without these. Each is a hard blocker for the request path.

### 1. TLS 1.3 — replaces the `ssl` module (blocks `httpx`, `stripe`, `boto3`, `redis` over TLS)

**The single highest-value port, and the one everything external waits on.**
Today the workspace refuses TLS deliberately (ADR-0017) because it would need a
crypto dependency. But `std::bignum` already ships modular arithmetic, and
`std::crypto` already ships SHA-256/HMAC with RFC test vectors — the two hard
halves of a handshake.

What is still missing: X25519, AES-GCM or ChaCha20-Poly1305, P-256 ECDSA
verification, and X.509 certificate parsing. Certificate chain validation is
where the real bugs live, and it is *exactly* the kind of pure, spec-shaped
logic tuonelang is good at pinning.

### 2. DNS resolution — replaces `socket.getaddrinfo` — ✅ done

Explicitly named in the tuonelang README as belonging *in tuonelang* on the UDP
primitives, which already exist (`std::net::udp_send` / `udp_recv`). This is a
small, self-contained, entirely achievable port: A/AAAA/CNAME record parsing
over UDP, with a wire-format parser that specs cleanly. Done first as a
warm-up — it was the shortest path to a real win, and it surfaced a runtime
bug (`udp_bind` on loopback) that ADR-0017 had to be amended for.

### 3. HTTP/1.1 client + server — replaces `httpx`, `requests`, `uvicorn`, `gunicorn`

`examples/http-service` already serves itself over a live loopback socket, so
the socket half is proven. What is needed is the full protocol: chunked transfer
encoding, keep-alive, header parsing, redirects, timeouts, connection pooling.
Pair it with TLS and the whole outbound-integration surface (Stripe, R2, Sentry)
becomes reachable.

### 4. FastAPI-equivalent routing + validation — replaces `fastapi` + `pydantic`

`examples/router` exists as a seed. This is the largest port by surface area but
the most natural fit: Pydantic is runtime type validation, and tuonelang has
*compile-time* types. Most of Pydantic's job disappears rather than being
reimplemented — the schema becomes a struct, and validation becomes parsing at
the boundary. Focus on: route matching, path/query extraction, JSON body
decoding into structs, and error responses.

### 5. SQLAlchemy-equivalent query layer — replaces `sqlalchemy` + `alembic`

`tuonelang-db` gives you the wire protocol. What is missing is the layer above:
typed row mapping, a query builder, connection pooling, transactions, and
migrations. Skip the ORM identity map and lazy loading — they need capturing
closures and recursive types, both refused today. Build a typed query builder
instead; it is a better fit for the language and a better tool anyway.

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
| **Redis client** | `redis` | RESP protocol is trivially simple over TCP; the natural next port after `tuolang-celery`, which currently models the broker rather than speaking to one. |
| **Structured logging + Sentry** | `sentry-sdk` | Needs only JSON + HTTP. Error capture, breadcrumbs, envelope format. |
| **JWT / JOSE** | (part of your auth) | HMAC-SHA256 already exists — HS256 is nearly free today. RS256 waits on TLS-era RSA. |
| **S3 client** | `boto3` | You only use Cloudflare R2. SigV4 signing is pure HMAC-SHA256 — already available. A tiny, targeted client beats all of boto3. |
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
3. **Redis client** — makes `tuolang-celery` talk to a real broker. **Next.**
4. **HTTP/1.1** — the backbone of everything outbound and inbound.
5. **TLS 1.3** — the big one; unlocks every external integration at once.
6. **Router + validation**, then **query layer** — the application framework.

Tiers 2 and 3 fall out largely for free once 4 and 5 exist.

## The migration tool

[`tools/py2tuo`](../tuonelang/tools/py2tuo) compiles a typed subset of Python to
tuonelang and refuses the rest with a positioned diagnostic. It is not a path to
porting these libraries — the semantic gap (exceptions, classes, closures,
arbitrary-precision ints) is too wide — but it is useful for mechanically
translating **pure leaf functions** during a port, with CPython as the
differential oracle.
