#!/bin/sh
# Backup everything that matters: the SQLite database (live-safe, via the .backup
# command) and the content-addressed artifact store. Run from the host, with the
# stack up or down.
#
#   ./scripts/backup.sh /path/to/backups
#
# Restoring: stop the stack, untar over ./data, start it again.
set -eu

repo="$(cd "$(dirname "$0")/.." && pwd)"
data="${SNITCH_DATA_DIR:-$repo/data}"
dest="${1:?usage: backup.sh <destination-dir>}"
stamp="$(date +%Y%m%d-%H%M%S)"
out="$dest/snitch-backup-$stamp"

mkdir -p "$out"
if [ -f "$data/snitch.db" ]; then
    sqlite3 "$data/snitch.db" ".backup '$out/snitch.db'"
    echo "database -> $out/snitch.db"
fi
if [ -f "$data/scheduler.db" ]; then
    sqlite3 "$data/scheduler.db" ".backup '$out/scheduler.db'"
    echo "scheduler -> $out/scheduler.db"
fi
if [ -d "$data/artifacts" ]; then
    cp -R "$data/artifacts" "$out/artifacts"
    echo "artifacts -> $out/artifacts"
fi
if [ -f "$repo/config.yaml" ]; then
    cp "$repo/config.yaml" "$out/config.yaml"
fi
tar -czf "$out.tar.gz" -C "$dest" "$(basename "$out")"
rm -rf "$out"
echo "done: $out.tar.gz"
