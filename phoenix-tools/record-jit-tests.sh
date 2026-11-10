#!/usr/bin/env bash
# Record IC generator runs over jit-tests with Phoenix's recorder (phoenix-docs/README.md).
#
#   phoenix-tools/record-jit-tests.sh [--build x64|arm] [--no-build] [--replay TABLE]
#                                     [--record-types TYPES] LABEL [KINDS] [-- JIT_TEST_ARGS...]
#
# - LABEL names the run, which goes in $PHOENIX_RUNS/<date>-LABEL. PHOENIX_RUNS defaults to
#   phoenix-runs next to this worktree.
# - KINDS is a comma-separated list of CacheKinds to record, e.g. UnaryArith. Without it, every
#   kind is recorded.
# - --record-types TYPES is a comma-separated list of the types of records to write, generator
#   and compile. Without it, both are written. A replay run needs only compile records.
# - Arguments after -- go to jit_test.py, e.g. test paths to run only those.
# - --build arm records the 32-bit ARM shell, obj-js-arm, under QEMU's user mode, with the
#   sysroot's libraries and QEMU_CPU (default cortex-a7). The default is x64, obj-js-x64.
# - --replay TABLE replaces the code of the stubs in TABLE (PHOENIX_REPLAY, see
#   phoenix-docs/docs/substitution.md). The table is copied into the run.
#
# It builds the shell first (skip with --no-build), runs jit-tests with --no-ion
# --baseline-eager, and dedupes the records into records/unique.jsonl.

set -euo pipefail

usage() {
  sed -n '2,20s/^# \{0,1\}//p' "$0" >&2
  exit 2
}

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
RUNS=${PHOENIX_RUNS:-$(dirname "$ROOT")/phoenix-runs}
command_line=$(printf '%q ' "$0" "$@")

backend=x64
build=1
replay=
types=
while [[ ${1:-} == --* && $1 != -- ]]; do
  case $1 in
    --record-types)
      [[ $# -ge 2 ]] || usage
      types=$2
      shift 2
      ;;
    --build)
      [[ $# -ge 2 && ($2 == x64 || $2 == arm) ]] || usage
      backend=$2
      shift 2
      ;;
    --no-build)
      build=0
      shift
      ;;
    --replay)
      [[ $# -ge 2 ]] || usage
      replay=$(realpath "$2")
      shift 2
      ;;
    *) usage ;;
  esac
done
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

OBJDIR=$ROOT/obj-js-$backend
JS=$OBJDIR/dist/bin/js
# What the shell runs under: nothing for x64, QEMU for ARM (phoenix-docs/docs/backends.md).
runner_env=()
if [[ $backend == arm ]]; then
  sysroot=$HOME/.mozbuild/sysroot-armhf-linux-gnu
  if [[ ! -e /proc/sys/fs/binfmt_misc/qemu-arm ]]; then
    echo "QEMU isn't registered for ARM programs: install qemu-user and qemu-user-binfmt" >&2
    exit 1
  fi
  if [[ ! -e $sysroot/lib/ld-linux-armhf.so.3 ]]; then
    echo "$sysroot has no ARM loader: make it with phoenix-tools/sysroot.py --arch armhf" >&2
    exit 1
  fi
  runner_env=(QEMU_LD_PREFIX="$sysroot" QEMU_CPU="${QEMU_CPU:-cortex-a7}")
  # jit_test.py's default of 150 s is too short for the slowest tests under QEMU, which take up
  # to 384 s. A --timeout after -- overrides it.
  set -- --timeout=600 "$@"
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
  if ! (cd "$ROOT" && MOZCONFIG=$ROOT/mozconfig-js-$backend ./mach build) >"$build_log" 2>&1; then
    echo "The build failed: see $build_log" >&2
    exit 1
  fi
fi

if ! grep -q '^#define JS_PHOENIX' "$OBJDIR/js/src/js-confdefs.h" 2>/dev/null; then
  echo "$JS isn't built with --enable-phoenix, so it can't record" >&2
  exit 1
fi

# The recorder crashes on a kind or record type that doesn't exist, and on a
# replay table it can't read. Find out now, not in every test.
recorder_env=(-u PHOENIX_RECORD_KINDS -u PHOENIX_RECORD_TYPES -u PHOENIX_REPLAY
  "${runner_env[@]}")
if [[ -n $kinds ]]; then
  recorder_env+=(PHOENIX_RECORD_KINDS="$kinds")
fi
if [[ -n $types ]]; then
  recorder_env+=(PHOENIX_RECORD_TYPES="$types")
fi
if [[ -n $replay ]]; then
  recorder_env+=(PHOENIX_REPLAY="$replay")
fi
if [[ -n $kinds || -n $types || -n $replay ]]; then
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
if [[ -n $replay ]]; then
  cp "$replay" "$run/replay.table"
  recorder_env+=(PHOENIX_REPLAY="$run/replay.table")
fi

echo "Running jit-tests, recording ${kinds:-every kind}${replay:+, replaying $replay}, into $run"
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
if [[ -n $replay ]]; then
  python3 "$ROOT/phoenix-tools/corpus.py" replay "$run/records" | tee "$run/replay-summary.txt"
fi
