#!/usr/bin/env bash
# run-tests.sh — the validation suite for tuonelang-python-tools.
#
#   ./run-tests.sh          front end, specs, formatting, the native Argon2
#                           and X.509 oracles, and the HTTP, web, and TLS
#                           loopback oracles (no network)
#   ./run-tests.sh --live   also resolve real names against a public DNS
#                           server, fetch from public HTTP servers, complete
#                           TLS 1.3 handshakes with three openssl s_server
#                           instances (Ed25519 pinned, P-256 and RSA-2048
#                           chains), fetch https:// from Stripe, Sentry, and
#                           Cloudflare with chains validated against the root
#                           store, and drive the Redis client against the
#                           docker compose redis on 127.0.0.1:6380 and the S3
#                           client against its MinIO on 127.0.0.1:9000, post to a
#                           Sentry-shaped loopback receiver, and run
#                           the query layer against a throwaway PostgreSQL that
#                           examples/sql-test/server.sh creates and deletes
#                           (needs initdb, pg_ctl, psql on PATH)
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
X509_SRC=(src/x509/chain.tuo src/x509/roots.tuo src/x509/certificate.tuo
          src/x509/fixture.tuo src/x509/time.tuo src/x509/name.tuo
          src/ec/field.tuo src/ec/curve.tuo src/ec/p256.tuo src/ec/p384.tuo
          src/ec/ecdsa.tuo src/rsa/verify.tuo src/crypto/sha384.tuo
          src/tls/handshake.tuo src/tls/fixture.tuo
          "${CRYPTO_STD[@]}" src/std_str.tuo src/std_bits.tuo)
HTTP_SRC=(src/http/message.tuo src/http/fixture.tuo src/http/url.tuo
          src/http/client.tuo src/http/server.tuo
          src/tls/client.tuo src/tls/clock.tuo
          src/dns/wire.tuo src/dns/name.tuo src/dns/message.tuo src/dns/record.tuo
          src/dns/resolver.tuo
          "${X509_SRC[@]}"
          src/std_net.tuo src/std_sync.tuo)

WEB_SRC=(src/web/json.tuo src/web/coerce.tuo src/web/errors.tuo src/web/schema.tuo
         src/web/query.tuo src/web/route.tuo src/web/request.tuo src/web/response.tuo
         src/web/fixture.tuo src/web/demo.tuo)

# The query layer stands on tuonelang-db's adapter, vendored as src/pg and
# src/db exactly as the catalog modules are.
PG_SRC=(src/db/bytes.tuo src/db/error.tuo src/pg/auth.tuo src/pg/conn.tuo
        src/pg/message.tuo src/pg/proto.tuo src/pg/query.tuo src/pg/value.tuo)
SQL_SRC=(src/sql/table.tuo src/sql/clause.tuo src/sql/select.tuo src/sql/write.tuo
         src/sql/migrate.tuo src/sql/fixture.tuo src/sql/demo.tuo src/sql/scram.tuo
         src/sql/url.tuo src/sql/row.tuo src/sql/session.tuo src/sql/pool.tuo
         "${PG_SRC[@]}"
         src/dns/wire.tuo src/dns/name.tuo src/dns/message.tuo src/dns/record.tuo
         src/dns/resolver.tuo
         src/std_net.tuo src/std_str.tuo src/std_crypto.tuo src/std_ct.tuo src/std_bits.tuo)

S3_SRC=(src/s3/crc32.tuo src/s3/sigv4.tuo src/s3/request.tuo src/s3/fixture.tuo
        src/s3/demo.tuo src/s3/xml.tuo src/s3/client.tuo
        "${HTTP_SRC[@]}")

LOG_SRC=(src/log/record.tuo src/log/format.tuo src/log/logger.tuo src/log/fixture.tuo
         src/log/demo.tuo src/web/json.tuo src/x509/time.tuo src/std_str.tuo)
SENTRY_SRC=(src/sentry/dsn.tuo src/sentry/scope.tuo src/sentry/event.tuo src/sentry/envelope.tuo
            src/sentry/fixture.tuo src/sentry/demo.tuo src/sentry/client.tuo
            src/log/record.tuo src/log/format.tuo src/log/logger.tuo src/log/fixture.tuo
            src/log/demo.tuo
            "${WEB_SRC[@]}" "${HTTP_SRC[@]}")

JINJA_SRC=(src/jinja/escape.tuo src/jinja/context.tuo src/jinja/lexer.tuo src/jinja/expr.tuo
           src/jinja/filters.tuo src/jinja/template.tuo src/jinja/fixture.tuo src/jinja/demo.tuo
           src/web/json.tuo src/web/coerce.tuo src/std_str.tuo)

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

step "X.509 and signatures: front end (check)"
"$TUO" check "${X509_SRC[@]}" examples/x509.tuo; check $? "x509 check"

step "X.509 and signatures: specs (verify)"
"$TUO" verify "${X509_SRC[@]}"; check $? "x509 verify"

step "HTTP and TLS: front end (check)"
"$TUO" check "${HTTP_SRC[@]}" examples/http.tuo examples/http_live.tuo examples/tls.tuo examples/tls_openssl.tuo examples/https_live.tuo; check $? "http+tls check"

step "HTTP and TLS: specs (verify)"
"$TUO" verify "${HTTP_SRC[@]}"; check $? "http+tls verify"

step "Web: front end (check)"
"$TUO" check "${WEB_SRC[@]}" "${HTTP_SRC[@]}" examples/web.tuo; check $? "web check"

# Only the web modules and what they stand on: the HTTP group's specs ran above.
step "Web: specs (verify), all 107 captured FastAPI responses replayed"
"$TUO" verify "${WEB_SRC[@]}" src/http/message.tuo src/http/fixture.tuo src/http/url.tuo src/std_str.tuo src/std_bits.tuo; check $? "web verify"

step "SQL: front end (check)"
"$TUO" check "${SQL_SRC[@]}" examples/sql.tuo; check $? "sql check"

step "SQL: specs (verify), all 117 captured SQLAlchemy and Alembic statements rebuilt"
"$TUO" verify "${SQL_SRC[@]}"; check $? "sql verify"

step "S3: front end (check)"
"$TUO" check "${S3_SRC[@]}" examples/s3.tuo; check $? "s3 check"

# The pure s3 modules and what they stand on; s3::client rides on the HTTP group, whose specs ran above.
step "S3: specs (verify), all 22 captured boto3 requests rebuilt"
"$TUO" verify src/s3/crc32.tuo src/s3/sigv4.tuo src/s3/request.tuo src/s3/fixture.tuo src/s3/demo.tuo src/s3/xml.tuo src/http/url.tuo src/http/message.tuo src/http/fixture.tuo src/std_crypto.tuo src/std_ct.tuo src/std_bits.tuo src/std_str.tuo; check $? "s3 verify"

step "Log: front end (check)"
"$TUO" check "${LOG_SRC[@]}"; check $? "log check"

step "Log: specs (verify), all 7 records formatted as CPython formats them"
"$TUO" verify "${LOG_SRC[@]}"; check $? "log verify"

step "Sentry: front end (check)"
"$TUO" check "${SENTRY_SRC[@]}" examples/sentry.tuo; check $? "sentry check"

# The pure sentry modules and what they stand on; sentry::client rides on the HTTP group.
step "Sentry: specs (verify), all 7 captured envelopes rebuilt"
"$TUO" verify src/sentry/dsn.tuo src/sentry/scope.tuo src/sentry/event.tuo src/sentry/envelope.tuo src/sentry/fixture.tuo src/sentry/demo.tuo "${LOG_SRC[@]}"; check $? "sentry verify"

step "Jinja: front end (check)"
"$TUO" check "${JINJA_SRC[@]}"; check $? "jinja check"

step "Jinja: specs (verify), all 100 captured renders reproduced"
"$TUO" verify "${JINJA_SRC[@]}"; check $? "jinja verify"

# Only this crate's own sources. The vendored std_*.tuo are verbatim catalog
# copies and are deliberately not reformatted — they must stay byte-identical
# to `crates/tuo-stdlib/src/std/` — and src/pg, src/db are tuonelang-db's.
step "Formatting"
"$TUO" fmt --check src/dns/*.tuo src/argon2/*.tuo src/redis/*.tuo src/http/*.tuo src/web/*.tuo src/tls/*.tuo src/x509/*.tuo src/ec/*.tuo src/rsa/*.tuo src/crypto/*.tuo src/sql/*.tuo src/s3/*.tuo src/log/*.tuo src/sentry/*.tuo src/jinja/*.tuo examples/*.tuo; check $? "fmt --check"

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

# ECDSA on the real curves and the chain walks over real certificates
# exceed the sandbox's fuel: RFC 6979's signatures, the P-256/P-384 and
# RSA-2048 test PKIs, and chains captured from Cloudflare, Sentry, and
# Stripe, validated against the root store at the capture date. No network.
step "X.509: native oracle (RFC 6979, the test PKIs, captured public chains)"
"$TUO" run examples/x509.tuo "${X509_SRC[@]}"
rc=$?
if [ "$rc" -eq 0 ]; then
  check 0 "x509 oracle (all checks agreed)"
else
  echo "x509 oracle exited $rc — that many checks disagreed"
  check 1 "x509 oracle"
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

# The FastAPI-shaped application behind http::server, every captured case
# replayed down one kept-alive connection.
step "Web: loopback oracle (web::demo behind http::server)"
"$TUO" run examples/web.tuo "${WEB_SRC[@]}" "${HTTP_SRC[@]}"
rc=$?
if [ "$rc" -eq 0 ]; then
  check 0 "web oracle (all checks agreed)"
else
  echo "web oracle exited $rc — that many checks disagreed"
  check 1 "web oracle"
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

  # The TLS interop oracle: three OpenSSL servers — the pinned Ed25519 test
  # certificate, the P-256 chain, and the RSA-2048 chain (examples/tls-test).
  step "TLS: live — against openssl s_server (pinned, P-256 chain, RSA chain)"
  if command -v openssl >/dev/null 2>&1; then
    openssl s_server -tls1_3 -cert examples/tls-test/cert.pem -key examples/tls-test/key.pem \
      -accept 4433 -www -naccept 2 -quiet >/dev/null 2>&1 &
    openssl_pid=$!
    openssl s_server -tls1_3 -cert examples/tls-test/ec-leaf.pem -key examples/tls-test/ec-leaf.key \
      -cert_chain examples/tls-test/ec-int.pem -accept 4434 -www -naccept 2 -quiet >/dev/null 2>&1 &
    openssl_ec_pid=$!
    openssl s_server -tls1_3 -cert examples/tls-test/big-leaf.pem -key examples/tls-test/big-leaf.key \
      -cert_chain examples/tls-test/big-int.pem -accept 4435 -www -naccept 1 -quiet >/dev/null 2>&1 &
    openssl_big_pid=$!
    sleep 1
    "$TUO" run examples/tls_openssl.tuo "${HTTP_SRC[@]}"
    rc=$?
    for pid in "$openssl_pid" "$openssl_ec_pid" "$openssl_big_pid"; do
      kill "$pid" >/dev/null 2>&1; wait "$pid" 2>/dev/null
    done
    if [ "$rc" -eq 0 ]; then
      check 0 "tls openssl (all checks agreed)"
    else
      echo "tls openssl exited $rc — that many checks disagreed"
      check 1 "tls openssl"
    fi
  else
    echo "no openssl on PATH; skipping"
  fi

  step "HTTPS: live — Stripe, Sentry, and Cloudflare through the root store"
  "$TUO" run examples/https_live.tuo "${HTTP_SRC[@]}"
  rc=$?
  if [ "$rc" -eq 0 ]; then
    check 0 "https live (all checks agreed)"
  else
    echo "https live exited $rc — that many checks disagreed"
    check 1 "https live"
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

  # The Sentry client against a Sentry-shaped receiver on loopback; live
  # only because its clock is SNTP's.
  step "Sentry: live — the client against a loopback receiver, clock from SNTP"
  "$TUO" run examples/sentry.tuo "${SENTRY_SRC[@]}"
  rc=$?
  if [ "$rc" -eq 0 ]; then
    check 0 "sentry live (all checks agreed)"
  else
    echo "sentry oracle exited $rc — that many checks disagreed"
    check 1 "sentry live"
  fi

  # The S3 client against shallowflaws's docker compose MinIO on 127.0.0.1:9000.
  step "S3: live — against MinIO on 127.0.0.1:9000"
  "$TUO" run examples/s3.tuo "${S3_SRC[@]}"
  rc=$?
  if [ "$rc" -eq 0 ]; then
    check 0 "s3 live (all checks agreed)"
  else
    echo "s3 oracle exited $rc — that many checks disagreed (is docker compose up?)"
    check 1 "s3 live"
  fi

  # The query layer against a real server: a cluster created for this run,
  # on its own port, demanding SCRAM, MD5, and cleartext of three roles.
  step "SQL: live — against a throwaway PostgreSQL on 127.0.0.1:54329"
  if command -v initdb >/dev/null 2>&1 && command -v pg_ctl >/dev/null 2>&1 && command -v psql >/dev/null 2>&1; then
    sql_dir="$(mktemp -d)/cluster"
    if examples/sql-test/server.sh start "$sql_dir" 2>/dev/null; then
      "$TUO" run examples/sql.tuo "${SQL_SRC[@]}"
      rc=$?
      examples/sql-test/server.sh stop "$sql_dir"
      if [ "$rc" -eq 0 ]; then
        check 0 "sql live (all checks agreed)"
      else
        echo "sql oracle exited $rc — that many checks disagreed"
        check 1 "sql live"
      fi
    else
      examples/sql-test/server.sh stop "$sql_dir"
      echo "could not start a PostgreSQL cluster (is port 54329 taken?)"
      check 1 "sql live"
    fi
  else
    echo "initdb, pg_ctl, or psql not found — skipped"
  fi
else
  printf '\n(skipping live DNS, HTTP, TLS, Redis, and SQL checks; pass --live to run them)\n'
fi

printf '\n'
if [ "$failed" -eq 0 ]; then echo "all checks passed"; else echo "SOME CHECKS FAILED"; fi
exit "$failed"
