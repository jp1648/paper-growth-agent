#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
TRADER_UID="$(id -u)"
TRADER_GID="$(id -g)"
export TRADER_UID TRADER_GID
exec docker compose "$@"
