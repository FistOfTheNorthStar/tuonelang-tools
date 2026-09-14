#!/usr/bin/env bash
# run-tests.sh — the validation suite for tuonelang-python-tools.
#
#   ./run-tests.sh          front end, specs, formatting, and the native
#                           Argon2 oracle (no network)
#   ./run-tests.sh --live   also resolve real names against a public DNS server
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

# Only this crate's own sources. The vendored std_*.tuo are verbatim catalog
# copies and are deliberately not reformatted — they must stay byte-identical
# to `crates/tuo-stdlib/src/std/`.
step "Formatting"
"$TUO" fmt --check src/dns/*.tuo src/argon2/*.tuo examples/*.tuo; check $? "fmt --check"

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
else
  printf '\n(skipping live DNS checks; pass --live to resolve real names)\n'
fi

printf '\n'
if [ "$failed" -eq 0 ]; then echo "all checks passed"; else echo "SOME CHECKS FAILED"; fi
exit "$failed"
