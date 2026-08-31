#!/usr/bin/env bash
# Pre-push / pre-commit safety scan.
#
# Attest sweeps a live AWS account, so its output contains real IAM user names,
# email addresses, resource ids and the account id. This repo is public. This
# script is the backstop that keeps those apart.
#
#   ./scripts/scan_repo.sh            scan tracked + staged files
#   ./scripts/scan_repo.sh --all      scan the whole working tree
#
# Install as a pre-commit hook:
#   ln -sf ../../scripts/scan_repo.sh .git/hooks/pre-commit

set -uo pipefail
# Resolve the repo root from git, not from $0: when this runs as a symlinked
# .git/hooks/pre-commit, dirname "$0" is .git/hooks and "$0/.." is .git.
cd "$(git rev-parse --show-toplevel)" || exit 1

RED=$'\033[31m'; GRN=$'\033[32m'; YEL=$'\033[33m'; RST=$'\033[0m'
fails=0

if [[ "${1:-}" == "--all" ]]; then
  files=$(git ls-files; git ls-files --others --exclude-standard)
else
  # staged files if any, else all tracked files
  files=$(git diff --cached --name-only --diff-filter=ACM)
  [[ -z "$files" ]] && files=$(git ls-files)
fi
# shellcheck disable=SC2206
files=($files)
[[ ${#files[@]} -eq 0 ]] && { echo "nothing to scan"; exit 0; }

# Documented placeholders that are safe by definition:
#   123456789012 / 111122223333  AWS's own documentation example account ids
#   *.example.com|org|net        RFC 2606 reserved domains
#   noreply@                     non-deliverable sender
ALLOWLIST='123456789012|111122223333|@example\.(com|org|net)|noreply@'

report() { # name, pattern, extra grep flags
  local name="$1" pattern="$2"; shift 2
  local hits
  # -H forces the filename prefix even when a single file is scanned, so the
  # self-exclusion below matches reliably. This script necessarily contains the
  # very patterns it searches for.
  hits=$(grep -HnIE "$pattern" "$@" -- "${files[@]}" 2>/dev/null \
         | grep -v '^\./scripts/scan_repo\.sh:' \
         | grep -v '^scripts/scan_repo\.sh:' \
         | grep -vE "$ALLOWLIST" || true)
  if [[ -n "$hits" ]]; then
    echo "${RED}FAIL${RST} $name"
    echo "$hits" | sed 's/^/      /' | head -20
    fails=$((fails + 1))
  else
    echo "${GRN} ok ${RST} $name"
  fi
}

echo "scanning ${#files[@]} files"
echo

# A 12-digit run is almost always an AWS account id. Excludes obvious version
# strings and the placeholder used in docs.
report "AWS account id"        '(^|[^0-9v.])[0-9]{12}([^0-9.]|$)'
report "AWS access key id"     '(AKIA|ASIA|AIDA|AROA)[0-9A-Z]{16}'
report "AWS secret access key" '(aws_secret_access_key|aws_session_token)[[:space:]]*[=:]'
report "private key block"     'BEGIN [A-Z ]*PRIVATE KEY'
report "AI attribution"        'Co-Authored-By:[[:space:]]*Claude|Generated with \[?Claude|Built with Claude'
# Emails belong to real people swept from the account. noreply@ and the MIT
# licence's boilerplate are the only acceptable forms.
report "email address"         '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}'

# .env must never be tracked
if git ls-files --error-unmatch .env >/dev/null 2>&1; then
  echo "${RED}FAIL${RST} .env is tracked by git"
  fails=$((fails + 1))
else
  echo "${GRN} ok ${RST} .env not tracked"
fi

# STATUS.md holds live-account observations; it must stay local
if git ls-files --error-unmatch STATUS.md >/dev/null 2>&1; then
  echo "${RED}FAIL${RST} STATUS.md is tracked (it is local-only working state)"
  fails=$((fails + 1))
else
  echo "${GRN} ok ${RST} STATUS.md not tracked"
fi

echo
if [[ $fails -gt 0 ]]; then
  echo "${RED}$fails check(s) failed — do not push.${RST}"
  echo "${YEL}If a hit is a false positive, narrow the pattern rather than skipping the scan.${RST}"
  exit 1
fi
echo "${GRN}all checks passed${RST}"
