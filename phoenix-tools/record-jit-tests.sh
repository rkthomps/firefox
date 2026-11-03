#!/usr/bin/env bash
# Record IC generator runs over jit-tests with Phoenix's recorder (phoenix-docs/RECORDER.md).
#
#   phoenix-tools/record-jit-tests.sh [--no-build] LABEL [KINDS] [-- JIT_TEST_ARGS...]
#
# - LABEL names the run, which goes in $PHOENIX_RUNS/<date>-LABEL. PHOENIX_RUNS defaults to
#   phoenix-runs next to this worktree.
# - KINDS is a comma-separated list of CacheKinds to record, e.g. UnaryArith. Without it, every
#   kind is recorded.
# - Arguments after -- go to jit_test.py, e.g. test paths to run only those.
#
# It builds obj-js-x64 first (skip with --no-build), runs jit-tests with --no-ion
# --baseline-eager, and dedupes the records into records/unique.jsonl.

set -euo pipefail

usage() {
  sed -n '2,13s/^# \{0,1\}//p' "$0" >&2
  exit 2
}

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
OBJDIR=$ROOT/obj-js-x64
JS=$OBJDIR/dist/bin/js
RUNS=${PHOENIX_RUNS:-$(dirname "$ROOT")/phoenix-runs}
command_line=$(printf '%q ' "$0" "$@")

build=1
if [[ ${1:-} == --no-build ]]; then
  build=0
  shift
fi
[[ $# -ge 1 && $1 != -- ]] || usage
label=$1
shift
kinds=
if [[ $# -ge 1 && $1 != -- ]]; then
  kinds=$1
  shift
fi
if [[ $# -ge 1 ]]; then
  [[ $1 == -- ]] || usage
  shift
fi

run=$RUNS/$(date +%F)-$label
if [[ -e $run ]]; then
  echo "$run already exists: pick another label" >&2
  exit 1
fi

build_log=$ROOT/artifacts/record-jit-tests-build.log
if ((build)); then
  echo "Building $OBJDIR (log: $build_log)"
  mkdir -p "$ROOT/artifacts"
  if ! (cd "$ROOT" && MOZCONFIG=$ROOT/mozconfig-js-x64 ./mach build) >"$build_log" 2>&1; then
    echo "The build failed: see $build_log" >&2
    exit 1
  fi
fi

if ! grep -q '^#define JS_PHOENIX' "$OBJDIR/js/src/js-confdefs.h" 2>/dev/null; then
  echo "$JS isn't built with --enable-phoenix, so it can't record" >&2
  exit 1
fi

# The recorder crashes on a kind that doesn't exist. Find out now, not in every test.
recorder_env=(-u PHOENIX_RECORD_KINDS)
if [[ -n $kinds ]]; then
  recorder_env+=(PHOENIX_RECORD_KINDS="$kinds")
  probe=$(mktemp -d)
  if ! (ulimit -c 0 && env "${recorder_env[@]}" PHOENIX_RECORD="$probe" "$JS" --baseline-eager \
    -e 'for (var i = 0; i < 2; i++) ~i' >/dev/null 2>"$probe/stderr") 2>/dev/null; then
    grep -m1 PHOENIX "$probe/stderr" >&2 || cat "$probe/stderr" >&2
    rm -rf "$probe"
    exit 1
  fi
  rm -rf "$probe"
fi

mkdir -p "$run/records"
echo "$command_line" >"$run/command.txt"
{
  git -C "$ROOT" rev-parse HEAD
  git -C "$ROOT" status --short
} >"$run/rev.txt"
if ((build)); then
  cp "$build_log" "$run/build.log"
fi

echo "Running jit-tests, recording ${kinds:-every kind}, into $run"
status=0
# jit_test.py writes main_raw.log into the current directory.
(cd "$run" && env "${recorder_env[@]}" PHOENIX_RECORD="$run/records" \
  python3 "$ROOT/js/src/jit-test/jit_test.py" "$JS" \
  --args="--no-ion --baseline-eager" --no-progress --format=automation \
  -w "$run/failures.txt" "$@") >"$run/jit-test.log" 2>&1 || status=$?

sed -n '/^Result summary:/,$p' "$run/jit-test.log"
if [[ -s $run/failures.txt ]]; then
  echo "Failed tests (also in failures.txt):"
  sed 's/^/  /' "$run/failures.txt"
fi
if ((status != 0 && status != 2)); then
  echo "jit_test.py exited with status $status: see $run/jit-test.log" >&2
fi

python3 "$ROOT/phoenix-tools/corpus.py" dedupe "$run/records" >"$run/summary.txt"
head -1 "$run/summary.txt"
echo "Summary by kind, stub and input types: $run/summary.txt"
