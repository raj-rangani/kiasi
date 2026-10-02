#!/bin/sh
case $0 in */*) dir=${0%/*} ;; *) dir=. ;; esac
script=$1
shift
has() { command -v "$1" >/dev/null 2>&1; }
if [ "${OS:-}" = "Windows_NT" ]; then
  if has py; then exec py -3 "$dir/$script" "$@"; fi
  if has python; then exec python "$dir/$script" "$@"; fi
fi
if has python3; then exec python3 "$dir/$script" "$@"; fi
if has python; then exec python "$dir/$script" "$@"; fi
echo "kiasi: Python 3.8 or newer was not found on PATH; the hook did nothing" >&2
exit 0
