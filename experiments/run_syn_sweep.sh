#!/usr/bin/env bash
# M6 T6.1 synthesis sweep: run syn/synth.sh over the job list, N at a time (default 3 nextpnr jobs).
# Usage: experiments/run_syn_sweep.sh [JOBS] ; job list on stdin or built-in (design P seed per line).
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/env.sh >/dev/null 2>&1 || true
J="${1:-3}"
{
  for s in $(seq 1 10); do echo "accel 8 $s"; done
  for s in $(seq 1 10); do echo "soc 8 $s"; done
  for s in 1 2 3; do for P in 1 2 4 16; do echo "accel $P $s"; done; done
  if [ -z "${NOBASE:-}" ]; then for s in $(seq 1 10); do echo "picorv32 8 $s"; echo "soc_noaccel 8 $s"; done; fi
} | { [ -n "${SKIP:-}" ] && grep -v -x -F "$SKIP" || cat; } \
  | xargs -P "$J" -L1 bash -c 'bash syn/synth.sh "$0" "$1" "$2" >/dev/null 2>&1 || echo "FAIL $0 $1 $2"'
