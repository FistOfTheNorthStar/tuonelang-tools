# Finding: `udp_bind` binds to loopback, which disables all outbound UDP

**Status:** resolved — option 1 below was taken. ADR-0017 was amended on
2026-09-08 and `tuo_rt_udp_bind` now binds `INADDR_ANY` (tuonelang commit
`362ff37`, merged in PR #60); the runtime's own test suite pins the new bind
address. `./run-tests.sh --live` resolves real names with a `tuo` built from
that commit or later. A binary built before it — including one installed
with `cargo install` and never refreshed — still fails every lookup with
`send failed`, which is the symptom to check for first.
**Found by:** writing `dns::resolver` against the ADR-0017 UDP primitives.
**Severity (when open):** blocked the entire stated purpose of the UDP
increment.

The rest of this document is the finding as filed, kept as the record of
why the bind address is what it is.

## What happens

Every `udp_send` to a non-loopback address fails with `NET_ERROR`:

```
loopback=5        // sendto 127.0.0.1 — fine
cloudflare=-1     // sendto 1.1.1.1:53 — fails
google=-1         // sendto 8.8.8.8:53 — fails
```

The network is fine; `nc -u 1.1.1.1 53` from the same shell gets a real DNS
response back.

## Why

`crates/tuo-runtime/src/effect.rs:858`, in `tuo_rt_udp_bind`:

```c
addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
```

Binding a socket's *local* address to `127.0.0.1` constrains its source
address, so the kernel refuses to route a datagram from it to any off-machine
destination. Reduced to C, with no tuonelang involved:

```
bound to 127.0.0.1:  sendto -> -1 (errno=49 Can't assign requested address)
bound to INADDR_ANY: sendto -> 5  (ok)
```

`EADDRNOTAVAIL` is the kernel saying the source address cannot reach that
destination — not a permissions or firewall matter.

## Why it looks deliberate but is not

ADR-0017 says binding stays loopback-only "for the same reason ADR-0014 gave:
no committed test or benchmark may open an **externally reachable port**."

That rationale is about **inbound** reachability, and it is a good one. But
`INADDR_LOOPBACK` on a client socket also severs **outbound** traffic, which
the rationale never asks for and which nothing in the ADR's threat model needs:
a socket bound to `INADDR_ANY:0` (an ephemeral port, never listened on) is not
externally reachable in any meaningful sense — nothing is accepting on it.

The same ADR states the intended use case directly:

> DNS should be written in tuonelang on the UDP primitives this ADR adds, not
> embedded in the runtime shim.

That cannot be done as the runtime stands. `udp_send`'s own doc comment
describes sending to an arbitrary `host`, and `tuo_rt_addr_parse` goes to the
trouble of parsing IPv4 and IPv6 destinations — machinery that is unreachable
in practice for every destination except one.

## Options

1. **Bind `udp_bind` to `INADDR_ANY`.** One line. Preserves the ADR's actual
   guarantee (no externally reachable *listening* port) because an unlistened
   ephemeral UDP socket accepts nothing meaningful. Makes the ADR's own stated
   DNS use case possible.

2. **Add `udp_bind_any`** alongside the existing loopback-only `udp_bind`,
   leaving current semantics untouched. Additive, and the ADR explicitly
   contemplates widening "when dogfooding demands it" — this is that demand.

3. **Leave it, and treat outbound UDP as out of scope** — in which case the
   ADR's DNS sentence should be struck, and `udp_send`'s `host` parameter
   documented as loopback-only.

Option 1 is the smallest change that makes the primitives do what the ADR says
they are for. Option 2 is the most conservative.

## Consequence for this crate

Everything in `src/dns/` is pure and proven by specs regardless — the parser,
the compression-pointer defences, and the resolver's `classify` judgement all
stand. Only `examples/resolve.tuo`, which puts a datagram on a real socket,
could not complete a lookup while this was open. It was committed and
correct, and it worked unchanged the moment the bind address did: the first
`--live` run after the fix resolved `example.com`, `cloudflare.com`, and
`github.com`, and reported `no A record` for a name that does not exist.
`src/std_net.tuo` was re-vendored from the amended catalog at the same time;
only its doc comments changed.
