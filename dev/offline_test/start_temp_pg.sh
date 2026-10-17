#!/usr/bin/env bash
# TEST-ONLY throwaway Postgres for offline testing of the GRAPH code (no pgvector).
# Uses the host's Postgres 14 *binaries* only — does NOT start or touch the system
# postgresql service. Data lives in esam/var/pgtest (gitignored). Localhost:55433,
# trust auth, user "tester", database "esamtest".
#   ./start_temp_pg.sh        start (creates cluster on first run)
#   ./start_temp_pg.sh stop   stop
#   ./start_temp_pg.sh wipe   stop and delete the data
set -euo pipefail
BIN=/usr/lib/postgresql/14/bin
DATA="$(cd "$(dirname "$0")/../.." && pwd)/var/pgtest"
case "${1:-start}" in
  stop) "$BIN/pg_ctl" -D "$DATA" stop -m fast ;;
  wipe) "$BIN/pg_ctl" -D "$DATA" stop -m fast 2>/dev/null || true; rm -rf "$DATA" ;;
  start)
    if [ ! -d "$DATA" ]; then mkdir -p "$DATA"; "$BIN/initdb" -D "$DATA" -U tester --auth=trust -E UTF8 >/dev/null; fi
    "$BIN/pg_ctl" -D "$DATA" -o "-p 55433 -k '' -c listen_addresses=127.0.0.1" -l "$DATA.log" start
    sleep 2
    psql -h 127.0.0.1 -p 55433 -U tester -d postgres -qtc "SELECT 1 FROM pg_database WHERE datname='esamtest'" | grep -q 1 \
      || psql -h 127.0.0.1 -p 55433 -U tester -d postgres -qc "CREATE DATABASE esamtest"
    echo "temp Postgres up on 127.0.0.1:55433 (db esamtest)" ;;
esac
