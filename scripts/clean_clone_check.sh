#!/usr/bin/env bash
# Clone the repo (with submodule) into a temp dir, bootstrap reusing this checkout's tools/, run make lint + make test.
# Committed state only: uncommitted changes in the working tree are NOT in the clone (listed below).
# Usage: scripts/clean_clone_check.sh [--slow]   (--slow adds make test-slow, hours)
set -uo pipefail
SRC="$(cd "$(dirname "$0")/.." && pwd)"
D="$(mktemp -d "${TMPDIR:-/tmp}/mcuaccel_clone.XXXXXX")"
echo "clone dir: $D"
git -C "$SRC" status --porcelain | sed 's/^/uncommitted (not tested): /'
git clone -q "$SRC" "$D/repo" || exit 2
cd "$D/repo"
# the clone's submodule URL is the origin's (a local path); use this checkout's submodule, no network
git config submodule.third_party/mcugrad.url "$SRC/third_party/mcugrad"
git -c protocol.file.allow=always submodule update --init || exit 2
# reuse tools/ without downloads (bootstrap.sh downloads only what is missing)
mkdir -p tools
for t in oss-cad-suite xpack-riscv-none-elf-gcc-15.2.0-1 python; do
  [ -e "$SRC/tools/$t" ] && ln -sfn "$(cd "$SRC/tools" && pwd -P)/$t" "tools/$t"
done
rc=0
run() { echo "=== $*"; "$@" || { echo "FAILED: $*"; rc=1; }; }
run scripts/bootstrap.sh
if [ $rc = 0 ]; then
  source scripts/env.sh
  run make lint
  run make test
  [ "${1:-}" = --slow ] && run make test-slow
fi
[ $rc = 0 ] && echo "CLEAN-CLONE-CHECK: PASS ($D/repo)" || echo "CLEAN-CLONE-CHECK: FAIL ($D/repo)"
exit $rc
