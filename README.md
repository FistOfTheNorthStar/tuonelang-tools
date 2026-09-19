# tuonelang-python-tools

**The Python dependency surface of `shallowflaws/`, ported to
[tuonelang](../tuonelang)** — one library at a time, in the order the
[roadmap](ROADMAP.md) sets, each proven by colocated specs against its
published test vectors.

Seven ports so far:

| Port | Replaces | Proven by |
|---|---|---|
| **`dns`** — a stub resolver on the v0 UDP primitives | `socket.getaddrinfo` | 82 specs, and a live lookup against a public server |
| **`argon2`** — Argon2id/i/d, BLAKE2b, and the PHC string format | `passlib[argon2]` | 125 specs, RFC 9106's vectors, and a hash the Python backend itself wrote |
| **`redis`** — a RESP2 client, the commands Celery and slowapi use, `redis://` URLs | `redis` (under `celery[redis]` and `slowapi`) | 40 specs against bytes a Redis 7 server sent, and a 35-check live oracle |
| **`http`** — HTTP/1.1 framing, a client with redirects, a keep-alive server | `httpx`, `requests`, `uvicorn` (behind Caddy) | 107 specs against bytes Cloudflare and gunicorn sent, an 18-check loopback oracle, an 11-check live one |
| **`tls`** — a TLS 1.3 client on the catalog's `std::tls`, and `https://` in the HTTP client | the `ssl` module | RFC 8448's trace reproduced from the client's side, a 12-check loopback oracle against `std::tls`, an 8-check interop oracle against three OpenSSL servers, and live `https://` to Stripe, Sentry, and Cloudflare |
| **`x509`**, **`ec`**, **`rsa`** — certificate parsing, chain validation, a root store, ECDSA on P-256/P-384, RSA PKCS#1 v1.5 and PSS, SHA-384 | the `ssl` module's trust half (`certifi`, `cryptography`'s verifier) | 380 specs (RFC 6979 and a test PKI natively), a 27-check oracle over chains captured from Cloudflare, Sentry, and Stripe |
| **`web`** — routing as Starlette does it, request models as data, Pydantic's lax validation, FastAPI's 422 body | `fastapi`, `pydantic`, `starlette`, `email-validator` (the request path) | 37 specs, and 107 responses captured from FastAPI 0.141 reproduced byte for byte — as a pure function, and again over a socket |

```bash
./run-tests.sh          # front end, the specs, formatting, the native Argon2 and X.509
                        # oracles, and the HTTP, web, and TLS loopback oracles (no network)
./run-tests.sh --live   # also resolve real names over UDP, fetch from public HTTP servers,
                        # complete TLS 1.3 handshakes with three openssl s_server instances,
                        # fetch https:// from Stripe, Sentry, and Cloudflare, and drive the
                        # Redis client against shallowflaws's docker compose redis on 6380
```

Both ports are pure tuonelang over the v0 core, with the catalog modules
they need vendored as compiler inputs (see [Layout](#layout)). Nothing is
embedded in the runtime.

---

## `argon2` — password hashing

`shallowflaws` stores every password as
`$argon2id$v=19$m=65536,t=3,p=4$<salt>$<tag>`, written by passlib over a
SHA-256 pre-hash of the password. This port produces and verifies exactly
those strings. The proof that matters is the last line of the native oracle:

```
ok    shallowflaws hash_password("testpassword") verifies at m=65536, t=3, p=4
```

That hash was produced by `app/api/auth_utils.py` on 2026-09-14, salt and
all, and `argon2::password::verify` accepts it — and rejects a
one-character variation — at the production memory cost, in about three
seconds of native code per verify.

### Status

| Layer | State |
|---|---|
| `argon2::word` — 64-bit words on a trapping signed `Int` | ✅ proven |
| `argon2::blake2b` — BLAKE2b, RFC 7693 | ✅ proven against RFC 7693 and `hashlib` |
| `argon2::hprime` — the variable-length hash H' | ✅ proven |
| `argon2::block` — the 1024-byte block and compression function G | ✅ proven |
| `argon2::index` — the reference-block rules, all three variants | ✅ proven, window by window |
| `argon2::hash` — the Argon2 operation | ✅ proven; RFC 9106 §5 asserted natively |
| `argon2::phc` — the `$argon2id$…` string, encode/decode/verify | ✅ proven byte-for-byte against argon2-cffi |
| `argon2::password` — the backend's pre-hash pipeline | ✅ proven; the backend's own hash asserted natively |

### The 64-bit problem

Argon2 and BLAKE2b are defined over unsigned 64-bit words with wrapping
addition. tuonelang's `Int` is a signed i64 whose `+` and `*` **trap** on
overflow (ADR-0026), and whose `>>` is arithmetic. Every word operation the
two RFCs use is therefore spelled out in `argon2::word`: addition through
32-bit halves, a logical shift that masks after the first bit, and — the one
Argon2 adds to BLAKE2b — a 32×32→64 multiply assembled from four 16-bit
partial products so that no intermediate ever needs the sign bit. A word
with its top bit set is simply a negative `Int` holding the right pattern.

### What the specs pin that a comment cannot

**The digest length is an input, not a truncation.** BLAKE2b folds the
output length into its first state word, so BLAKE2b-256 of a message is not
a prefix of BLAKE2b-512 of it. H' adds a second length prefix of its own.
An implementation that got either wrong would agree with itself perfectly
and with no other implementation; the specs assert `hashlib.blake2b` at
lengths 1, 4, 32, and 64, and a Python transcription of H' at 4, 32, 64, 65,
100, and 1024.

**`G(X, X)` is the zero block, for every X.** The compression function
xors its input into its output, and the permutation fixes zero — so two
equal inputs produce nothing. The spec pins this identity alongside a
vector, because the identity is what a wrong row/column addressing would
break first.

**Which blocks may be referenced.** RFC 9106 §3.4.2 describes the reference
window in prose that the reference implementation's `index_alpha` spells
out in four cases. `argon2::index::area_size` is spec'd with hand-derived
window sizes for every case at the smallest legal geometry — first pass and
later, same lane and other, first block of a segment and not — because an
off-by-one here still produces a hash, just not Argon2.

**The PHC string is byte-for-byte argon2-cffi's.** `argon2::phc::encode`
of a tag computed here equals the string argon2-cffi's `hash_secret` returns
for the same inputs, unpadded Base64 and all; and `decode` refuses
`shallowflaws`'s own `$argon2id$v=19$m=65536,t=3,p=4$fakehash` test fixture
along with a missing leading `$`, an unknown variant, `v=16`, and the cost
parameters out of order.

### The fuel boundary

The spec sandbox grants ten million interpreted instructions per assertion.
A memory-hard hash is built to spend more, and RFC 9106's own vectors (32
KiB, three passes, four lanes) exceed it during initialisation. So the specs
prove every layer at the smallest legal geometry — one lane, eight blocks —
where argon2-cffi vectors for all three variants, a 100-byte tag, and an
empty password all fit, and `examples/argon2.tuo` asserts the rest natively:
the three RFC tags, six interim block values, two multi-lane vectors, and
the backend's hash. ADR-0020 draws the same line for PBKDF2's iteration
count. `run-tests.sh` runs the oracle by default; it needs no network.

### What is deliberately not here

The lanes are filled sequentially. The RFC allows the segments of a slice
to run in parallel, and `std::sync::par_map` could fork them, but it cannot
share the memory array between workers without an effect — and the tag is
identical either way. Drawing a salt is an effect, so
`argon2::password::hash` takes one; the example shows the shape. Version
`0x10` is not spoken; nothing in the backend has ever written it. The secret
and associated-data inputs are plumbed through `argon2::hash` (the RFC
vectors use both) but not through `argon2::password`, because passlib does
not use them.

---

## `redis` — the broker's wire protocol

`shallowflaws` reaches Redis through two libraries. Celery's transport is
lists — `LPUSH` to publish, `BRPOP` to consume — plus sets and hashes for
bindings and unacknowledged deliveries, and `SET ... EX` / `GET` for task
results. slowapi's rate limiter is `INCR` and `EXPIRE`. This port is a
RESP2 client with exactly those commands as typed builders, a parser for
the `redis://` URLs the backend is configured by, and an effect boundary
that reads each reply one socket read at a time under the parser's
direction. It is the piece [`tuolang-celery`](../tuolang-celery) said it
lacked: its broker is modelled in memory because there was no RESP client.

### Status

| Layer | State |
|---|---|
| `redis::resp` — RESP2 framing, a flat reply arena, `needed` | ✅ proven against server bytes |
| `redis::command` — the two dozen commands the backend uses | ✅ proven byte-for-byte |
| `redis::url` — `redis://[user][:password@]host[:port][/db]` | ✅ proven on the repo's own `.env` shapes |
| `redis::client` — open, send, read one reply, close, and typed round trips | ✅ 35 live checks against Redis 7.4 |

### What the specs pin that a comment cannot

**A bulk string is framed by its length, not by its line.** The fixture
`$12\r\nline1\r\nline2\r\n` is a value with the protocol's own
terminator inside it, and the spec asserts the parser returns all twelve
bytes. Reading to the next `\r\n` — the tempting implementation — would
return five and leave the connection desynchronised on the next reply.

**The parser says how much to read.** `redis::resp::needed` is pure and
returns `0` for a complete reply, `-1` for one that can never complete, and
otherwise a lower bound on the bytes still missing — exact for a bulk
payload (`$5\r\nhel` needs 4), one for a line whose length is not yet
known. The effect layer loops on that number and contains no framing logic
at all, so the only code that decides where a reply ends is code a spec can
reach.

**Nested replies are a flat arena.** `EXEC` answers with an array of
replies, one of which may be an array. v0 refuses recursive types, so a
reply is a preorder node list with parent links, as `std::json` does, and
`child_at` is spec'd to skip a nested array's own elements when counting
the root's.

**Every fixture is what the server sent.** Each reply the `resp` specs
parse — `+OK`, `$-1`, `*-1`, `-WRONGTYPE ...`, the `EXEC` array — is the
literal byte string a Redis 7.4 server returned over a socket on
2026-09-14. The parser is checked against Redis, not against itself.

### The live oracle

`examples/redis.tuo` drives `redis::client` against the docker compose
Redis in database 15 under `tuo:oracle:` keys: a 5000-byte value framed
across several reads, a value containing `\r\n`, the empty value
distinguished from a fallback, a `BRPOP` that times out and one that
returns, a `WRONGTYPE` error that leaves the connection usable, a
`MULTI`/`EXEC` transaction, and `open_url` on the compose URL — and
refusing `rediss://` — the TLS session exists now, and wiring it in is a small follow-up.

### What is deliberately not here

`rediss://` parses and is refused at `open_url` (the TLS session is there; the flag is not wired yet). No name
resolution in the client: `std::net::connect` takes a numeric address, and
joining `dns::resolver` to it is a small future change. No RESP3 or
`HELLO`; Redis 7 speaks RESP2 by default and the backend's libraries use
it. No pipelining helper — `call` is one round trip, though `end_of` on a
parsed reply is what a pipeline reader would loop on. No pub/sub. And no
kombu message format: this is the transport, and the Celery message
envelope on top of it belongs with `tuolang-celery`.

---

## `tls` — a TLS 1.3 client that reaches the public web

The tuonelang catalog landed a TLS 1.3 *server* stack on 2026-09-15 —
X25519, ChaCha20-Poly1305, Ed25519 certificates, verified live against an
OpenSSL client — and every primitive under it. What it did not have was a
client, which is the half the roadmap needs: it is what `https://` in
`http::client` runs on, and the layer under every outbound integration.
This port is that client, built on the catalog's record protection, key
schedule, and DER decoder, with the handshake logic pure and the socket
sequence a thin loop over it — and, since 2026-09-16, the trust layer
under it: `x509` parses and validates chains against a root store, `ec`
and `rsa` verify the signatures public CAs actually make. `get` of an
`https://` URL now reaches Stripe, Sentry, and Cloudflare.

### Status

| Layer | State |
|---|---|
| `tls::handshake` — ClientHello, ServerHello, the server's flight, the key schedule, the three checks, `Trust` | ✅ proven; RFC 8448 reproduced from the client's side, its RSA-PSS CertificateVerify verified |
| `tls::client` — the session: handshake over a socket, records to bytes, tickets skipped | ✅ 12 loopback checks against `std::tls`, 8 against OpenSSL 3.6 |
| `tls::clock` — the time of day, over SNTP, since the runtime's clock is monotonic only | ✅ decode spec'd; live against `time.cloudflare.com` |
| `x509::certificate`, `x509::chain`, `x509::name`, `x509::time` — parse, walk, match, date | ✅ 190 specs on a test PKI and real certificates |
| `x509::roots` — 19 roots from the Mozilla bundle `certifi` ships | ✅ every entry spec'd to be a self-issued CA |
| `ec::field`, `ec::curve`, `ec::p256`, `ec::p384`, `ec::ecdsa` — Barrett arithmetic, Jacobian group law, ECDSA | ✅ 120 specs on small moduli and a 19-point curve; RFC 6979 natively |
| `rsa::verify`, `crypto::sha384` — PKCS#1 v1.5, RSASSA-PSS, MGF1, SHA-384 | ✅ 30 specs on OpenSSL signatures and FIPS digests |
| `http::client::get` — `https://` validated against the root store | ✅ live to `api.stripe.com`, `sentry.io`, `api.cloudflare.com`, `www.cloudflare.com` |

### What the specs pin that a comment cannot

**The key schedule is the RFC's, from the client's chair.** RFC 8448
prints every secret of one handshake. From its ECDHE result and its
messages, the specs derive the same handshake secrets, the same IVs, the
same application secrets, and — byte for byte — the client's Finished. The
RFC's cipher is AES-GCM, so the record keys are not comparable; every
hash, secret, IV, and MAC is, and every one is asserted. Its
CertificateVerify is `rsa_pss_rsae_sha256` under a 1024-bit key, and
that now verifies too, inside the sandbox. The X25519 step exceeds the
sandbox's fuel and is asserted natively by the loopback oracle instead.

**Our ClientHello is one the server accepts.** The catalog's own
`hello_acceptable`, `hello_offers_suite`, `hello_offers_tls13`, and
`hello_key_share` — the server-side readers an OpenSSL client has satisfied
— are run over the hello this client builds, and its ServerHello reply is
parsed back and accepted. A HelloRetryRequest is recognised by its random
and refused rather than mistaken for a second hello.

**Three checks, each spec'd to fail.** The certificates must satisfy the
`Trust` — a pinned Ed25519 key compared in constant time and never
matched by an empty pin, or a chain `x509::chain` validates; the
CertificateVerify must sign the transcript through the Certificate with
the leaf's key, under a scheme that fits it (Ed25519, ECDSA on the key's
own curve, or RSA-PSS — never PKCS#1 v1.5, which TLS 1.3 forbids there);
the Finished must MAC the transcript through the CertificateVerify. Each
has a spec that passes on the right input and one that fails on the
wrong transcript, data, scheme, or key.

**A chain validates for the reasons RFC 5280 gives, and fails for each
one.** `x509::chain::validate` returns a verdict, and the specs walk an
RSA-1024 test PKI through every one: ok for `localhost`, `EXAMPLE.test`,
and `a.wild.test`; name mismatch for `wild.test` (a wildcard matches one
label, never zero) and `example.com`; not current before `notBefore` and
after `notAfter`; untrusted under the wrong root, with no root, without
the intermediate, and with one signature byte flipped; no leaf for an
empty or malformed list. The walk is depth-first over the bag the server
sent, so a cross-signed bag with two routes to two roots is handled by
trying both — `api.stripe.com` sends five certificates that way and
validates — and honours `CA:TRUE`, `keyCertSign`, and `pathlen`.

**A certificate is read strictly.** Unknown critical extensions reject
it, as §4.2 says they must, and so does a critical name-constraints
extension this crate does not yet enforce; a duplicate extension, a
mismatch between the inner and outer signature algorithms, a version
that cannot carry extensions, and every truncation are `ok` false, never
a trap. Only DNS names are read from the alternative names, and a
certificate without them names no host: the Common Name fallback is not
offered.

**The arithmetic is checked where a person can check it.** `ec::field`'s
Barrett constants and reductions are asserted limb by limb for one-,
two-, and three-limb moduli against Python's integers; Knuth's Algorithm
D divides multi-limb numbers with the quotients Python gives; the group
law runs on the 19-point curve `y² = x³ + 2x + 2` over F₁₇, every
multiple of whose generator is listed in the spec, and an ECDSA
signature on it (d = 7, k = 3, e = 10 → (10, 14)) verifies and its six
corruptions do not. P-256's and P-384's parameters are pinned by their
Barrett constants and by `2G`, compared in Jacobian form because one
inversion on P-384 is past the fuel.

### The oracles

`examples/tls.tuo` forks a server on the catalog's `std::tls` and a client
on `http::client::get_pinned` in one process and joins them: a full
handshake, an HTTPS request answered through the record layer, the same
server refused under a wrong pin before any Finished is sent.
`examples/x509.tuo` runs natively what the sandbox cannot: RFC 6979's
P-256/SHA-256 and P-384/SHA-384 signatures verify and their swaps do
not; the P-256-under-P-384 and RSA-2048 test PKIs validate and fail for
the wrong host, time, and root; and chains captured from
`api.cloudflare.com`, `www.cloudflare.com`, `sentry.io`,
`api.stripe.com`, and `r2.cloudflarestorage.com` on 2026-09-15 validate
against `x509::roots` at that date — the R2 leaf, which had expired six
days earlier, is refused as not current and accepted a fortnight before.
`examples/tls_openssl.tuo` fetches from three `openssl s_server`
instances `run-tests.sh --live` starts: the pinned Ed25519 certificate,
the P-256 chain (an ECDSA CertificateVerify from an independent
implementation), and the RSA-2048 chain (RSA-PSS), each validated against
the test root in `examples/tls-test/` and refused under the wrong one.
`examples/https_live.tuo` is the point of it all: the time from
`time.cloudflare.com` over SNTP, then `GET https://api.stripe.com/v1/charges`
(401, as it should be without a key), `https://sentry.io/`,
`https://api.cloudflare.com/client/v4/`, and `https://www.cloudflare.com/`,
each chain validated against the root store, plus a public server
refused under a pin and under an empty store.

### What is deliberately not here

**There is no unverified mode.** `https://` needs a pin, or roots and a
time; with neither, or when the SNTP query fails, the request is refused
before connecting. **The time comes from the network** because
`std::rt::now_nanos` is monotonic with an arbitrary epoch and the runtime
has no wall clock; a process that knows the time passes it to
`tls::handshake::trust_roots` directly. **Not enforced:** name
constraints (critical ones reject the certificate), policies, revocation
(CRL, OCSP), Certificate Transparency, IP-address names. **Not spoken:**
HelloRetryRequest, resumption, 0-RTT, key updates, client certificates,
any key exchange but X25519, any suite but ChaCha20-Poly1305 — every
public server tried accepts that pair, and a server that does not is
refused at the ServerHello. **The root store is 19 roots, not 140:** the
ones the backend's integrations chain to plus the other large public
CAs; adding one is one base64 line. And the catalog's own caveat carries
over: `std::bignum` is variable-time, so the client's X25519 step leaks
timing on its ephemeral key. Signature *verification* is variable-time
by design, and there is no signing function in `ec` or `rsa` at all.

---

## `web` — FastAPI's request path

The backend is 107 FastAPI routes over Pydantic models. What a client of
it sees is decided by four things: which route a path and method reach,
what the models make of the input, what a refusal looks like, and what
the handler returns. The first three are the framework, and they are
this port: `web::route` resolves as Starlette does, `web::schema`
validates as Pydantic does in its default lax mode, and `web::errors`
renders FastAPI's 422 — the `{"detail":[{"type","loc","msg","input","ctx"}]}`
body the React frontend maps onto form fields — byte for byte.

### Status

| Layer | State |
|---|---|
| `web::json` — a JSON reader that keeps source spans, with CPython's error names and positions; a writer | ✅ proven |
| `web::coerce` — lax `int`/`float`/`bool` conversion, character counts, `email-validator`'s checks and reasons | ✅ proven against Pydantic 2.13 |
| `web::schema`, `web::errors` — models as data, validation of bodies and of query and path parameters, the 422 body | ✅ proven |
| `web::query`, `web::route` — query strings; ordered routes, path parameters, 404, 405 with `Allow`, the trailing-slash 307 | ✅ proven |
| `web::request`, `web::response` — the handler's view of a request; JSON, `detail`, and refusal responses | ✅ proven |
| `web::demo` — a FastAPI application reimplemented on the above | ✅ 107 of 107 captured responses reproduced, in specs and over a socket |

### The oracle is FastAPI itself

`examples/web_oracle.py` is a FastAPI app shaped like the backend — a
registration model with the constraints of
`JobApplicantRegistrationRequest`, a search with validated query
parameters, path parameters, routes whose order matters — and a list of
107 requests. Run in `shallowflaws`'s own virtualenv it records what
FastAPI 0.141, Pydantic 2.13, Starlette 1.6, and CPython 3.14 answered;
`src/web/fixture.tuo` is that recording. `web::demo` is the same app on
`web::*`, and its spec replays every request and compares status,
`Allow`, `Location`, and body. It was broken on purpose once to see the
spec fail. `examples/web.tuo` then serves `web::demo::handle` behind
`http::server` and replays all 107 down one kept-alive connection.

```bash
cd examples && ../../shallowflaws/.venv/bin/python web_oracle.py   # rewrites oracle.json
```

### What the specs pin that a comment cannot

**Invalid JSON fails where CPython says it fails.** FastAPI's 422 for a
malformed body carries `loc: ["body", N]` and CPython's message. The
reader is structured like `json.decoder`, so `{"email":"a@b.co",}` is
"Illegal trailing comma before end of object" at 17, `{"age":01}` is
"Expecting ',' delimiter" at 8 — the `0` parsed, the `1` did not belong —
and positions count characters, not bytes, so an `é` before the error is
one. Eighteen such cases are asserted.

**Lax mode is generous in exact ways.** `"30"`, `30.0`, and `true` are
all valid for an `int` field; `30.5` is `int_from_float`, `"thirty"` is
`int_parsing`, `[1]` is `int_type`. `"yes"`, `"On"`, `1`, and `1.0` are
`True`; `" yes "` and `2` are not. `"2.00"` is an int and `"2."` is not;
`"1_0"` is ten and `"1__0"` is an error. Every one of these was probed
against Pydantic first and is a spec line.

**An error echoes its input, exactly.** `"input":91.5`, `"input":-91`,
`"input":{"email":"jane@example.com","tags":[1,2]}` — compact, with the
number's own spelling, which is why the reader keeps spans rather than
floats. `ctx` is typed by the field: `{"le":90.0}` for a float,
`{"lt":150}` for an int, while the message says `90` either way.
Lengths count characters: seven `é` are too short for `min_length=8`,
eight are not.

**Email refusals give `email-validator`'s reason, in its order.** No
`@`-sign, nothing before it, nothing after it, invalid characters (listed,
sorted, a space shown as `SPACE`), the period rules, the hyphen rules, a
label over 63 characters, no period in the domain, a numeric top-level
domain, a reserved one. The domain is lowercased; the local part is not.

**Routing is Starlette's, where it is surprising too.** Order is the
only precedence, so `/jobs/search` must be registered before
`/jobs/{job_id}`. A parameter is matched after percent-decoding, so
`logo%20one.png` arrives as `logo one.png` and `a%2Fb` does not match at
all. `/jobs/5/` is a 307 to `/jobs/5` with the query kept, even when the
method would then be refused. `HEAD` is not implied by `GET`.

### What is deliberately not here

**Handlers are dispatched by the application**, with a `match` on the
slot `resolve` returns: v0's growable arrays cannot hold function values
(the finding `examples/router` documents), so a route table cannot carry
them. **Two departures from Starlette, both stated in `web::route`:**
`Allow` lists every method registered for the path rather than the first
route's, and the 307's `Location` is a path rather than an absolute URL.
**Three from Pydantic, stated in `web::coerce`:** integers beyond 64 bits
are refused, `nan` and `inf` are refused, and a float is rendered from
its source spelling rather than by shortest round-trip. **Not modelled:**
nested models, enums and literals, dates, `extra="forbid"`, custom
validators (a handler calls its own after `validate_body`), headers and
cookies as parameters, form bodies, dependency injection, OpenAPI,
middleware, CORS, WebSockets. Email addresses are the ASCII rules with
non-ASCII bytes allowed through: no IDNA, no display names, no quoted
local parts. The 107 routes themselves are the application, and they
wait on the query layer.

---

## `http` — the wire under everything

In production `shallowflaws` is uvicorn on port 8000 behind Caddy, which
terminates TLS and forwards plain HTTP/1.1 — so a plain HTTP/1.1 server is
exactly the production shape, and a plain client is the layer TLS will sit
on. This port is both, over one pure message layer: `http::message` frames
and parses, `http::url` takes URLs apart, `http::client` resolves names
through this crate's own `dns::resolver` and follows redirects, and
`http::server` accepts, reads, hands the request to a handler, and keeps
the connection alive for the next one.

### Status

| Layer | State |
|---|---|
| `http::message` — start lines, headers, RFC 9112 §6.3 body framing, chunked, building | ✅ proven against server bytes |
| `http::url` — `http://host[:port]/path?query`, percent-encoding, `urlencode` | ✅ proven |
| `http::client` — resolve, connect, one request, read to the parser's word, redirects | ✅ 11 live checks through DNS |
| `http::server` — accept, read, handle, keep-alive, 400/408/413 on its own | ✅ 18 loopback checks, served and fetched in one process |

### What the specs pin that a comment cannot

**Which of four framings applies.** RFC 9112 §6.3 decides a body's length
by an ordered list — no body for `HEAD`/1xx/204/304, then
`Transfer-Encoding`, then `Content-Length`, then "until close" for a
response and "none" for a request. `http::message::body_rule` is that list
and each step is spec'd, including the two that matter for security: a
request carrying **both** `Transfer-Encoding` and `Content-Length` is
invalid (the request-smuggling shape), and two `Content-Length` values that
disagree are invalid. The server answers both with `400` before any handler
runs; the loopback oracle sends the smuggling shape and checks.

**The parser says how much to read.** As with `redis::resp`,
`http::message::needed` is pure and returns `0`, `-1`, "until close", or a
lower bound on the bytes still missing — exact for a `Content-Length` body
and for each chunk. Both effect layers loop on that number and contain no
framing logic; a chunked Cloudflare page cut at byte 400 is spec'd to need
exactly 462 more.

**A header cannot be injected.** `http::message::header_line` refuses —
writes nothing for — a name that is not a token or a value containing
`\r` or `\n`, so a user-controlled string cannot end the header block or
add a `Set-Cookie` of its own. The spec asserts the empty result.

**Redirects change the method the way the RFC says.** `303` always becomes
`GET`; `301`/`302` do for `POST`; `307`/`308` never. A `Location` that is a
path is resolved against the origin; a relative one stops the chain with
the last response in hand. A redirect loop ends after
`max_redirects` with the last `302`, not a hang.

### The two oracles

`examples/http.tuo` listens on an ephemeral loopback port, forks a server
and a client with `std::sync::par_map`, and joins them: a 200 000-byte body
each way, a chunked response dechunked, `302` and `303` followed, two
pipelined requests on one kept-alive connection answered in order, a
`Connection: close` honoured, a malformed request line and the smuggling
shape both `400`. It needs no network and `run-tests.sh` runs it by
default. `examples/http_live.tuo` resolves `example.com` through
`dns::resolver`, fetches it, follows httpbin's redirect, posts JSON to it,
confirms an unresolvable name fails cleanly, and fetches `https://example.com/`
through the root store.

### What is deliberately not here

One request per client
connection — the server keeps connections alive, the client does not pool
them, and `http::message::message_end` is what a pool would loop on. One
connection at a time on the server; `par_map` could fork accepted
connections but cannot share a listener's state without an effect. No
HTTP/2, no `Expect: 100-continue`, no compression, no `Upgrade`. The
`router` and validation layers the roadmap lists next sit on top of
`http::server`'s handler, which receives the raw request and returns the
raw response.

### A compiler finding, since fixed

Writing the loopback oracle surfaced a code-generation refusal: a
temporary owned value in the right operand of `&&` or `||` failed MIR
verification natively while `tuo check` accepted it. The same bug was
found and fixed independently in tuonelang the same day; the reduction and
its resolution are in [`docs/COMPILER-FINDING.md`](docs/COMPILER-FINDING.md).

---

## `dns` — name resolution

The resolver ADR-0017 says belongs *in tuonelang* on the UDP primitives,
rather than in the runtime shim. It replaces `socket.getaddrinfo` — the name
resolution every HTTP client, database driver, and API call needs before it
can open a connection.

### Status

| Layer | State |
|---|---|
| `dns::wire` — big-endian codec | ✅ proven |
| `dns::name` — labels and compression pointers | ✅ proven, including the attacks |
| `dns::message` — header, flags, query building | ✅ proven |
| `dns::record` — answer walk, A records | ✅ proven |
| `dns::resolver` — the effect boundary | ✅ proven; ✅ resolves real names |

The live lookup was blocked until 2026-09-08 by a runtime bug this crate
found: `udp_bind` bound to loopback, which severs all outbound UDP. ADR-0017
was amended and the runtime fixed that day; the finding, its C-level
reduction, and the resolution are kept in
[`docs/RUNTIME-FINDING.md`](docs/RUNTIME-FINDING.md). A `tuo` binary built
before the fix still fails every lookup with `send failed`.

### Why DNS was the first port

It is small enough to finish, it needs nothing outside the v0 core, and it
is almost entirely *parsing untrusted bytes* — which is where colocated specs
earn their keep. A DNS resolver reads attacker-influenced input on every
single call, so its correctness properties are security properties.

### The three things the specs pin that a comment cannot

**1. Decompression terminates.** RFC 1035 §4.1.4 lets a name end in a pointer
to somewhere earlier in the message. A pointer that aims at itself, or at
another pointer aiming back, is the classic decompression denial of service.
`dns::name::decode` makes it structurally impossible rather than carefully
avoided: every pointer must aim **strictly backwards**, so each hop lands nearer
zero and a cycle cannot be constructed at all.

```tuo
then std::string::as_str(decode_pointer_cycle()) == "!";
```

That this spec *returns* is the proof. A resolver that loops here does not fail
the test — it never finishes it.

**2. A name's wire length is not its text length.** A pointer-terminated name
occupies two bytes however long the name it denotes, so a record walk that
advanced by the decoded text would desynchronise and misread every later
record.

```tuo
then compressed_len_at(13) == 6;    // not the 15 characters it decodes to
```

**3. RDLENGTH is attacker-chosen.** An `A` record claiming a length other than
4 is refused rather than read as whatever four bytes happen to follow it, and
one claiming more rdata than the datagram holds is refused rather than read
past the end.

```tuo
then std::string::as_str(lying_rdlength(65535)) == "!";
```

`truncated_at` runs *every prefix* of a real response through the whole parser.
None of them may trap, and the specs returning is what proves it.

### What the resolver checks

`dns::resolver::classify` is pure, so the security judgement is spec'd rather
than asserted:

| Check | Why |
|---|---|
| The message id matches the one sent | The forgery defence. An off-path attacker must guess it — and the ids come from `std::rt::random_byte`, never a counter. |
| `QR` is set | A query reflected back is not an answer. |
| `TC` is its own outcome | A truncated response is a *partial* one. Treating it as complete is how a resolver silently drops records — so it is reported, not ignored. |
| `NXDOMAIN` is distinct from failure | It is a definitive answer. Retrying it asks a question already settled. |

### What is deliberately not here

`AAAA` and `CNAME` are parsed as types but not yet resolved to addresses;
`dns::record` reads `A` records only. There is no cache — a TTL is decoded but
nothing yet honours it. And there is no TCP fallback, so a truncated response
is reported rather than retried. Each is additive on what is here.

---

## Layout

```
src/dns/wire.tuo         byte order, with Option decoders for short datagrams
src/dns/name.tuo         labels, the 63/255 limits, compression pointers
src/dns/message.tuo      the 12-byte header and the packed flags word
src/dns/record.tuo       the answer walk and A-record extraction
src/dns/resolver.tuo     the effect boundary: sockets in, judgement pure
examples/resolve.tuo     the live oracle (needs UDP to port 53)

src/argon2/word.tuo      64-bit words on a trapping Int; little-endian bytes
src/argon2/blake2b.tuo   BLAKE2b (RFC 7693), unkeyed, any digest length
src/argon2/hprime.tuo    the variable-length hash H' (RFC 9106 §3.3)
src/argon2/block.tuo     the 1024-byte block, GB, P, G, and the flat memory
src/argon2/index.tuo     which earlier block each new one references (§3.4)
src/argon2/hash.tuo      the Argon2 operation (§3.2) and its parameters
src/argon2/phc.tuo       the $argon2id$… string: encode, decode, verify
src/argon2/password.tuo  shallowflaws's hash_password / verify_password
examples/argon2.tuo      the native oracle (no network)

src/redis/resp.tuo       RESP2: encode a command, parse a reply, say what is missing
src/redis/command.tuo    the commands Celery and slowapi send, as bytes
src/redis/url.tuo        redis:// and rediss:// URLs
src/redis/client.tuo     the effect boundary: open, send, read one reply, close
examples/redis.tuo       the live oracle (needs the docker compose redis on 6380)

src/http/message.tuo     HTTP/1.1: start lines, headers, body framing, chunked, building
src/http/fixture.tuo     responses Cloudflare and gunicorn sent, byte for byte
src/http/url.tuo         http:// URLs, percent-encoding, urlencode
src/http/client.tuo      resolve through dns, connect, one request, redirects
src/http/server.tuo      accept, read under the parser's direction, handle, keep alive
examples/http.tuo        the loopback oracle: server and client in one process
examples/http_live.tuo   the live oracle: public servers through dns::resolver

src/web/json.tuo         a span-keeping JSON reader with CPython's errors; a writer
src/web/coerce.tuo       Pydantic's lax conversions; email-validator's checks
src/web/errors.tuo       FastAPI's 422 body
src/web/schema.tuo       request models as data; validation of bodies and parameters
src/web/query.tuo        query strings
src/web/route.tuo        the route table, resolved as Starlette resolves
src/web/request.tuo      a parsed request; is it JSON; the bearer token
src/web/response.tuo     JSON, detail, 422, 404/405/307, HEAD
src/web/fixture.tuo      107 responses captured from FastAPI (generated)
src/web/demo.tuo         the oracle's FastAPI app, reimplemented
examples/web.tuo         the socket oracle: web::demo behind http::server
examples/web_oracle.py   the FastAPI app and requests the fixture was captured from

src/tls/handshake.tuo    the client's half of the handshake, as pure data; Trust
src/tls/client.tuo       the session: handshake over a socket, bytes over records
src/tls/clock.tuo        the time of day over SNTP
src/tls/fixture.tuo      RFC 8448's trace, and the test certificate and its key
examples/tls.tuo         the loopback oracle: this client against std::tls
examples/tls_openssl.tuo the interop oracle: this client against three openssl s_servers
examples/https_live.tuo  the live oracle: https:// to Stripe, Sentry, Cloudflare
examples/tls-test/       the test certificates and chains as PEM, for openssl s_server

src/x509/certificate.tuo the fields of a certificate a client needs, read strictly
src/x509/chain.tuo       flat certificate lists, and the walk to a root
src/x509/name.tuo        DNS name matching, wildcards as RFC 6125 allows
src/x509/time.tuo        UTCTime and GeneralizedTime to Unix seconds
src/x509/roots.tuo       19 roots from the Mozilla bundle
src/x509/fixture.tuo     three test PKIs and five captured public chains
src/ec/field.tuo         Barrett reduction, Knuth division, pow, invert
src/ec/curve.tuo         Jacobian group law, spec'd on a 19-point curve
src/ec/p256.tuo          NIST P-256
src/ec/p384.tuo          NIST P-384
src/ec/ecdsa.tuo         ECDSA verification, strict DER signatures
src/rsa/verify.tuo       PKCS#1 v1.5 and RSASSA-PSS verification
src/crypto/sha384.tuo    SHA-384 on std::sha512's parts
examples/x509.tuo        the native oracle: RFC 6979, the test PKIs, captured chains

docs/COMPILER-FINDING.md the && / || temporary native codegen refused (fixed upstream)
docs/COMPILER-FINDING-2.md a local shadowing a module path drops the function silently

docs/RUNTIME-FINDING.md  the udp_bind finding, and its resolution
```

`src/std_bits.tuo`, `src/std_str.tuo`, `src/std_net.tuo`, `src/std_crypto.tuo`,
`src/std_ct.tuo`, `src/std_sync.tuo`, and — for the TLS client — `std_tls`,
`std_hkdf`, `std_chacha`, `std_x25519`, `std_ed25519`, `std_der`,
`std_sha512`, and `std_bignum` are **verbatim copies** of the catalog modules in
`crates/tuo-stdlib/src/std/`, vendored because v0 has no registry and passed
as compiler inputs — exactly as `examples/postgres-auth` documents. They are
byte-identical; edit the catalog, not these, and they are excluded from
`fmt --check` for that reason. `std_crypto` is here for SHA-256 (the
backend's pre-hash), Base64, hex, and the constant-time `verify`; `std_ct`
is what that `verify` is built on; `std_sync` is `par_map`, which the HTTP
loopback oracle forks its server and client with.
