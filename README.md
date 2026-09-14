# tuonelang-python-tools

**The Python dependency surface of `shallowflaws/`, ported to
[tuonelang](../tuonelang)** — one library at a time, in the order the
[roadmap](ROADMAP.md) sets, each proven by colocated specs against its
published test vectors.

Two ports so far:

| Port | Replaces | Proven by |
|---|---|---|
| **`dns`** — a stub resolver on the v0 UDP primitives | `socket.getaddrinfo` | 82 specs, and a live lookup against a public server |
| **`argon2`** — Argon2id/i/d, BLAKE2b, and the PHC string format | `passlib[argon2]` | 125 specs, RFC 9106's vectors, and a hash the Python backend itself wrote |

```bash
./run-tests.sh          # front end, 207 specs, formatting, the native Argon2 oracle
./run-tests.sh --live   # also resolve real names over UDP
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

docs/RUNTIME-FINDING.md  the udp_bind finding, and its resolution
```

`src/std_bits.tuo`, `src/std_str.tuo`, `src/std_net.tuo`, `src/std_crypto.tuo`,
and `src/std_ct.tuo` are **verbatim copies** of the catalog modules in
`crates/tuo-stdlib/src/std/`, vendored because v0 has no registry and passed
as compiler inputs — exactly as `examples/postgres-auth` documents. They are
byte-identical; edit the catalog, not these, and they are excluded from
`fmt --check` for that reason. `std_crypto` is here for SHA-256 (the
backend's pre-hash), Base64, hex, and the constant-time `verify`; `std_ct`
is what that `verify` is built on.
