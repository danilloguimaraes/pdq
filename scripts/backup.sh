#!/usr/bin/env bash
# Backup datado de data/ (banco + fotos) em backups/<YYYYMMDD-HHMMSS>/.
# Uso: scripts/backup.sh [--data-dir DIR] [--backups-dir DIR]
set -euo pipefail
cd "$(dirname "$0")/.."
exec python -m pdq backup "$@"
