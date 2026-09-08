#!/usr/bin/env bash
# run-tests.sh — the validation suite for tuonelang-dns.
#
#   ./run-tests.sh          front end, specs, and formatting (no network)
#   ./run-tests.sh --live   also resolve real names against a public server
#
# TUO may be set to a `tuo` binary; otherwise the one on PATH is used.
#
# The --live oracle currently fails on every lookup, and the cause is NOT in
# this crate: `tuo_rt_udp_bind` binds to INADDR_LOOPBACK, which severs all
# outbound UDP. See docs/RUNTIME-FINDING.md.
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
SRC=(src/dns/wire.tuo src/dns/name.tuo src/dns/message.tuo src/dns/record.tuo
     src/dns/resolver.tuo src/std_bits.tuo src/std_str.tuo src/std_net.tuo)

failed=0
step() { printf '\n=== %s ===\n' "$1"; }
check() { if [ "$1" -eq 0 ]; then echo "PASS $2"; else echo "FAIL $2"; failed=1; fi }

step "Front end (check)"
"$TUO" check "${SRC[@]}" examples/resolve.tuo; check $? "check"

step "Specs (verify)"
"$TUO" verify "${SRC[@]}"; check $? "verify"

# Only this crate's own sources. The vendored std_*.tuo are verbatim catalog
# copies and are deliberately not reformatted — they must stay byte-identical
# to `crates/tuo-stdlib/src/std/`.
step "Formatting"
"$TUO" fmt --check src/dns/*.tuo examples/resolve.tuo; check $? "fmt --check"

if [ "$live" -eq 1 ]; then
  step "Live: resolve real names"
  "$TUO" run examples/resolve.tuo "${SRC[@]}"
  rc=$?
  if [ "$rc" -eq 0 ]; then
    check 0 "resolve (all lookups succeeded)"
  else
    echo "resolve exited $rc — see docs/RUNTIME-FINDING.md"
    check 1 "resolve"
  fi
else
  printf '\n(skipping live checks; pass --live to resolve real names)\n'
fi

printf '\n'
if [ "$failed" -eq 0 ]; then echo "all checks passed"; else echo "SOME CHECKS FAILED"; fi
exit "$failed"
