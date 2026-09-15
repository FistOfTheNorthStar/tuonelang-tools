#!/usr/bin/env bash
# run-tests.sh — the validation suite for tuonelang-python-tools.
#
#   ./run-tests.sh          front end, specs, formatting, the native Argon2
#                           oracle, and the HTTP and TLS loopback oracles
#                           (no network)
#   ./run-tests.sh --live   also resolve real names against a public DNS
#                           server, fetch from public HTTP servers, complete
#                           a TLS 1.3 handshake with openssl s_server, and
#                           drive the Redis client against the docker compose
#                           redis on 127.0.0.1:6380
#
# TUO may be set to a `tuo` binary; otherwise the one on PATH is used.
#
# The --live step needs a `tuo` built from tuonelang at or after the ADR-0017
# amendment of 2026-09-08 (`udp_bind` on INADDR_ANY); an older binary fails
# every lookup with "send failed". See docs/RUNTIME-FINDING.md.
set -uo pipefail

TUO="${TUO:-tuo}"

live=0
for arg in "$@"; do
  case "$arg" in
    --live) live=1 ;;
    *) echo "unknown flag: $arg" >&2; exit 2 ;;
  esac
done

if ! command -v "$TUO" >/dev/null 2>&1 && [ ! -x "$TUO" ]; then
  echo "error: no \`tuo\` binary found. Set TUO=/path/to/tuo." >&2
  exit 2
fi

# The vendored std_*.tuo modules are inputs, exactly as examples/postgres-auth
# passes its own; v0 has no registry.
DNS_SRC=(src/dns/wire.tuo src/dns/name.tuo src/dns/message.tuo src/dns/record.tuo
         src/dns/resolver.tuo src/std_bits.tuo src/std_str.tuo src/std_net.tuo)
ARGON2_SRC=(src/argon2/word.tuo src/argon2/blake2b.tuo src/argon2/hprime.tuo
            src/argon2/block.tuo src/argon2/index.tuo src/argon2/hash.tuo
            src/argon2/phc.tuo src/argon2/password.tuo
            src/std_crypto.tuo src/std_ct.tuo src/std_bits.tuo src/std_str.tuo)
REDIS_SRC=(src/redis/resp.tuo src/redis/command.tuo src/redis/url.tuo
           src/redis/client.tuo src/std_str.tuo src/std_net.tuo)
CRYPTO_STD=(src/std_tls.tuo src/std_hkdf.tuo src/std_chacha.tuo src/std_x25519.tuo
            src/std_ed25519.tuo src/std_der.tuo src/std_sha512.tuo src/std_bignum.tuo
            src/std_ct.tuo src/std_crypto.tuo)
HTTP_SRC=(src/http/message.tuo src/http/fixture.tuo src/http/url.tuo
          src/http/client.tuo src/http/server.tuo
          src/tls/handshake.tuo src/tls/client.tuo src/tls/fixture.tuo
          src/dns/wire.tuo src/dns/name.tuo src/dns/message.tuo src/dns/record.tuo
          src/dns/resolver.tuo
          "${CRYPTO_STD[@]}"
          src/std_str.tuo src/std_net.tuo src/std_bits.tuo src/std_sync.tuo)

failed=0
step() { printf '\n=== %s ===\n' "$1"; }
check() { if [ "$1" -eq 0 ]; then echo "PASS $2"; else echo "FAIL $2"; failed=1; fi }

step "DNS: front end (check)"
"$TUO" check "${DNS_SRC[@]}" examples/resolve.tuo; check $? "dns check"

step "DNS: specs (verify)"
"$TUO" verify "${DNS_SRC[@]}"; check $? "dns verify"

step "Argon2: front end (check)"
"$TUO" check "${ARGON2_SRC[@]}" examples/argon2.tuo; check $? "argon2 check"

step "Argon2: specs (verify)"
"$TUO" verify "${ARGON2_SRC[@]}"; check $? "argon2 verify"

step "Redis: front end (check)"
"$TUO" check "${REDIS_SRC[@]}" examples/redis.tuo; check $? "redis check"

step "Redis: specs (verify)"
"$TUO" verify "${REDIS_SRC[@]}"; check $? "redis verify"

step "HTTP and TLS: front end (check)"
"$TUO" check "${HTTP_SRC[@]}" examples/http.tuo examples/http_live.tuo examples/tls.tuo examples/tls_openssl.tuo; check $? "http+tls check"

step "HTTP and TLS: specs (verify)"
"$TUO" verify "${HTTP_SRC[@]}"; check $? "http+tls verify"

# Only this crate's own sources. The vendored std_*.tuo are verbatim catalog
# copies and are deliberately not reformatted — they must stay byte-identical
# to `crates/tuo-stdlib/src/std/`.
step "Formatting"
"$TUO" fmt --check src/dns/*.tuo src/argon2/*.tuo src/redis/*.tuo src/http/*.tuo src/tls/*.tuo examples/*.tuo; check $? "fmt --check"

# The RFC 9106 tags and the backend's own 64 MiB hash exceed the spec
# sandbox's instruction fuel, so they are asserted natively. No network.
step "Argon2: native oracle (RFC 9106 §5 and the backend's own hash)"
"$TUO" run examples/argon2.tuo "${ARGON2_SRC[@]}"
rc=$?
if [ "$rc" -eq 0 ]; then
  check 0 "argon2 oracle (all checks agreed)"
else
  echo "argon2 oracle exited $rc — that many checks disagreed"
  check 1 "argon2 oracle"
fi

# The HTTP server and client prove each other over loopback, in one process.
step "HTTP: loopback oracle (server and client in one process)"
"$TUO" run examples/http.tuo "${HTTP_SRC[@]}"
rc=$?
if [ "$rc" -eq 0 ]; then
  check 0 "http oracle (all checks agreed)"
else
  echo "http oracle exited $rc — that many checks disagreed"
  check 1 "http oracle"
fi

# The TLS client and the catalog's TLS server prove each other over loopback.
step "TLS: loopback oracle (client against std::tls, in one process)"
"$TUO" run examples/tls.tuo "${HTTP_SRC[@]}"
rc=$?
if [ "$rc" -eq 0 ]; then
  check 0 "tls oracle (all checks agreed)"
else
  echo "tls oracle exited $rc — that many checks disagreed"
  check 1 "tls oracle"
fi

if [ "$live" -eq 1 ]; then
  step "DNS: live — resolve real names"
  "$TUO" run examples/resolve.tuo "${DNS_SRC[@]}"
  rc=$?
  if [ "$rc" -eq 0 ]; then
    check 0 "resolve (all lookups succeeded)"
  else
    echo "resolve exited $rc — see docs/RUNTIME-FINDING.md"
    check 1 "resolve"
  fi

  step "HTTP: live — public servers through dns::resolver"
  "$TUO" run examples/http_live.tuo "${HTTP_SRC[@]}"
  rc=$?
  if [ "$rc" -eq 0 ]; then
    check 0 "http live (all checks agreed)"
  else
    echo "http live exited $rc — that many checks disagreed"
    check 1 "http live"
  fi

  # The TLS interop oracle: an OpenSSL server with the test certificate.
  step "TLS: live — against openssl s_server"
  if command -v openssl >/dev/null 2>&1; then
    openssl s_server -tls1_3 -cert examples/tls-test/cert.pem -key examples/tls-test/key.pem \
      -accept 4433 -www -naccept 2 -quiet >/dev/null 2>&1 &
    openssl_pid=$!
    sleep 1
    "$TUO" run examples/tls_openssl.tuo "${HTTP_SRC[@]}"
    rc=$?
    kill "$openssl_pid" >/dev/null 2>&1; wait "$openssl_pid" 2>/dev/null
    if [ "$rc" -eq 0 ]; then
      check 0 "tls openssl (all checks agreed)"
    else
      echo "tls openssl exited $rc — that many checks disagreed"
      check 1 "tls openssl"
    fi
  else
    echo "no openssl on PATH; skipping"
  fi

  # The Redis oracle needs shallowflaws's docker compose redis on 127.0.0.1:6380.
  step "Redis: live — against 127.0.0.1:6380"
  "$TUO" run examples/redis.tuo "${REDIS_SRC[@]}"
  rc=$?
  if [ "$rc" -eq 0 ]; then
    check 0 "redis (all checks agreed)"
  else
    echo "redis oracle exited $rc — that many checks disagreed (is docker compose up?)"
    check 1 "redis"
  fi
else
  printf '\n(skipping live DNS and Redis checks; pass --live to run them)\n'
fi

printf '\n'
if [ "$failed" -eq 0 ]; then echo "all checks passed"; else echo "SOME CHECKS FAILED"; fi
exit "$failed"
