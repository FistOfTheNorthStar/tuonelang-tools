# tuonelang-dns

**DNS in [tuonelang](../tuonelang)** — the resolver ADR-0017 says belongs in
the language rather than the runtime shim, written against the v0 UDP
primitives and proven by 82 colocated specs.

This is the first port on the [roadmap](ROADMAP.md) for replacing the Python
dependency surface of `shallowflaws/`. It replaces `socket.getaddrinfo` — the
name resolution every HTTP client, database driver, and API call needs before
it can open a connection.

```bash
./run-tests.sh          # 82 specs, front end, formatting
./run-tests.sh --live   # also resolve real names (see the caveat below)
```

## Status

| Layer | State |
|---|---|
| `dns::wire` — big-endian codec | ✅ proven |
| `dns::name` — labels and compression pointers | ✅ proven, including the attacks |
| `dns::message` — header, flags, query building | ✅ proven |
| `dns::record` — answer walk, A records | ✅ proven |
| `dns::resolver` — the effect boundary | ✅ judgement proven; ⚠️ blocked on a runtime bug |

**The live lookup does not work yet, and the cause is not in this crate.**
`tuo_rt_udp_bind` binds to `INADDR_LOOPBACK`, which severs all outbound UDP —
so `examples/resolve.tuo` compiles, checks, and is correct, but cannot get a
datagram off the machine. The finding, the C-level reduction that proves it,
and three options for fixing it are in
[`docs/RUNTIME-FINDING.md`](docs/RUNTIME-FINDING.md). It is a one-line change,
but it belongs in the `tuonelang` repo.

## Why DNS is a good first port

It is small enough to finish, it needs nothing outside the v0 core, and it is
almost entirely *parsing untrusted bytes* — which is where colocated specs earn
their keep. A DNS resolver reads attacker-influenced input on every single
call, so its correctness properties are security properties.

## The three things the specs pin that a comment cannot

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

## What the resolver checks

`dns::resolver::classify` is pure, so the security judgement is spec'd rather
than asserted:

| Check | Why |
|---|---|
| The message id matches the one sent | The forgery defence. An off-path attacker must guess it — and the ids come from `std::rt::random_byte`, never a counter. |
| `QR` is set | A query reflected back is not an answer. |
| `TC` is its own outcome | A truncated response is a *partial* one. Treating it as complete is how a resolver silently drops records — so it is reported, not ignored. |
| `NXDOMAIN` is distinct from failure | It is a definitive answer. Retrying it asks a question already settled. |

The truncation spec is the one worth reading: its fixture carries a valid
answer *and* the `TC` bit, so a resolver that checked the answer section first
would return an address and quietly lose the rest of the record set.

## Layout

```
src/dns/wire.tuo      byte order, with Option decoders for short datagrams
src/dns/name.tuo      labels, the 63/255 limits, compression pointers
src/dns/message.tuo   the 12-byte header and the packed flags word
src/dns/record.tuo    the answer walk and A-record extraction
src/dns/resolver.tuo  the effect boundary: sockets in, judgement pure
examples/resolve.tuo  the live oracle
```

`src/std_bits.tuo`, `src/std_str.tuo`, and `src/std_net.tuo` are **verbatim
copies** of the catalog modules in `crates/tuo-stdlib/src/std/`, vendored
because v0 has no registry and passed as compiler inputs — exactly as
`examples/postgres-auth` documents. They are byte-identical; edit the catalog,
not these, and they are excluded from `fmt --check` for that reason.

## What is deliberately not here

`AAAA` and `CNAME` are parsed as types but not yet resolved to addresses;
`dns::record` reads `A` records only. There is no cache — a TTL is decoded but
nothing yet honours it. And there is no TCP fallback, so a truncated response
is reported rather than retried. Each is additive on what is here, and none is
worth building before the runtime can send a datagram at all.
