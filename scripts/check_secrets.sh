#!/usr/bin/env bash
# Fail if staged (or, with --all, tracked) files look like they contain credentials.
set -euo pipefail
if [ "${1:-}" = "--all" ]; then files=$(git ls-files); else files=$(git diff --cached --name-only --diff-filter=ACM); fi
[ -z "$files" ] && exit 0
pat='(AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9_-]{20,}|ASIA[0-9A-Z]{16}|pypi-[A-Za-z0-9_-]{30,}|npm_[A-Za-z0-9]{30,}|ghp_[A-Za-z0-9]{30,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)'
bad=0
for f in $files; do
  [ -f "$f" ] || continue
  if grep -IEn "$pat" "$f" >/dev/null 2>&1; then echo "possible secret in $f"; bad=1; fi
done
exit $bad
