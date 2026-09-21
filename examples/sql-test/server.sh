#!/usr/bin/env bash
# server.sh — a throwaway PostgreSQL for the sql oracle.
#
#   examples/sql-test/server.sh start <dir>   initdb + start on 127.0.0.1:54329
#   examples/sql-test/server.sh stop <dir>    stop it and delete <dir>
#
# The cluster is created fresh each time and never touches another
# database: its own data directory, its own port, its socket inside <dir>.
# Three roles exist so that every password method the client answers is
# demanded by a real server:
#
#   tuo        SCRAM-SHA-256   (what a stock PostgreSQL 14+ asks of everyone)
#   tuo_md5    MD5
#   tuo_clear  cleartext
set -euo pipefail

action="${1:?start or stop}"
dir="${2:?a data directory}"
port=54329

case "$action" in
  start)
    rm -rf "$dir"
    mkdir -p "$dir"
    printf 'tuo-oracle-pw\n' > "$dir.pw"
    initdb -D "$dir/data" -U tuo --auth-local=trust --auth-host=scram-sha-256 \
      --pwfile="$dir.pw" --encoding=UTF8 --locale=C >/dev/null
    rm -f "$dir.pw"
    cat > "$dir/data/pg_hba.conf" <<HBA
local all all                      trust
host  all tuo_md5   127.0.0.1/32   md5
host  all tuo_clear 127.0.0.1/32   password
host  all all       127.0.0.1/32   scram-sha-256
HBA
    pg_ctl -D "$dir/data" -l "$dir/log" -w \
      -o "-p $port -c listen_addresses=127.0.0.1 -c unix_socket_directories=$dir -c fsync=off" \
      start >/dev/null
    psql -h "$dir" -p "$port" -U tuo -d postgres -q -v ON_ERROR_STOP=1 <<SQL
CREATE DATABASE tuo_oracle;
SET password_encryption = 'md5';
CREATE ROLE tuo_md5 LOGIN PASSWORD 'md5-pw';
SET password_encryption = 'scram-sha-256';
CREATE ROLE tuo_clear LOGIN PASSWORD 'clear-pw';
GRANT ALL ON DATABASE tuo_oracle TO tuo_md5, tuo_clear;
SQL
    ;;
  stop)
    pg_ctl -D "$dir/data" -m immediate stop >/dev/null 2>&1 || true
    rm -rf "$dir"
    ;;
  *)
    echo "unknown action: $action" >&2
    exit 2
    ;;
esac
