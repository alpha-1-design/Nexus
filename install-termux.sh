#!/usr/bin/env bash
#
# Nexus — Termux (Android) installer
#
# Installs Nexus into an isolated venv at ~/.nexus/venv with the
# tui + dashboard extras (the tested Termux target), puts the nexus
# command on your PATH and runs a health check.
#
# No sudo/apt is ever used — Termux owns its own package namespace.
# Safe to run again: every step is idempotent.
#
# Docs & issues: https://github.com/alpha-1-design/Nexus/issues

set -euo pipefail

readonly NEXUS_DIR="${NEXUS_DIR:-$HOME/.nexus}"
readonly VENV_DIR="$NEXUS_DIR/venv"
readonly REPO_URL="https://github.com/alpha-1-design/Nexus"
readonly ISSUES_URL="https://github.com/alpha-1-design/Nexus/issues"
readonly EXTRAS="tui,dashboard"
readonly MIN_PY="3.10"

# Directory this script lives in — the Nexus checkout to install from.
# Resolved robustly so `bash /path/to/install-termux.sh` works anywhere.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]:-$0}")" >/dev/null 2>&1 && pwd -P)"

# ── output plumbing ──────────────────────────────────────────────
if [ -t 1 ] && [ "${TERM:-}" != "dumb" ] && [ -z "${NO_COLOR:-}" ]; then
  BOLD=$'\033[1m'; DIM=$'\033[2m'
  RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; CYAN=$'\033[36m'
  OFF=$'\033[0m'
else
  BOLD=''; DIM=''; RED=''; GREEN=''; YELLOW=''; CYAN=''; OFF=''
fi

step() { printf '  %s▸%s %s\n' "$CYAN"   "$OFF" "$1"; }
ok()   { printf '  %s✓%s %s\n' "$GREEN"  "$OFF" "$1"; }
warn() { printf '  %s⚠%s %s\n' "$YELLOW" "$OFF" "$1"; }
fail() { printf '  %s✖%s %s\n' "$RED"    "$OFF" "$1"; }
hint() { printf '    %s·%s %s\n' "$DIM"  "$OFF" "$1"; }

# ── version helpers (keep identical to install-laptop.sh) ────────
# Extract a dotted version number from arbitrary tool output, e.g.
# "Python 3.11.4" → "3.11.4", "Python 3.10.0rc1" → "3.10.0".
extract_version() {
  printf '%s\n' "$1" | grep -oE '[0-9]+\.[0-9]+(\.[0-9]+)?' | head -n 1
}

# Numeric compare: ver_ge <have> <min>  e.g. ver_ge "3.11.4" "3.10"
ver_ge() {
  local have="$1" min="$2" i x y
  local -a h=() m=()
  local IFS='.'
  read -r -a h <<< "$have"
  read -r -a m <<< "$min"
  for i in 0 1 2; do
    x="${h[i]-0}"; y="${m[i]-0}"
    x="${x%%[!0-9]*}"; y="${y%%[!0-9]*}"
    x="${x:-0}"; y="${y:-0}"
    if [ "$((10#$x))" -gt "$((10#$y))" ]; then return 0; fi
    if [ "$((10#$x))" -lt "$((10#$y))" ]; then return 1; fi
  done
  return 0
}

# Print "MAJOR.MINOR.PATCH" for a python interpreter, or fail.
python_version() {
  local py="$1" out ver
  if out="$("$py" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null)"; then
    ver="$(extract_version "$out")" || return 1
    [ -n "$ver" ] && { printf '%s\n' "$ver"; return 0; }
  fi
  out="$("$py" --version 2>&1)" || return 1
  ver="$(extract_version "$out")" || return 1
  [ -n "$ver" ] && { printf '%s\n' "$ver"; return 0; }
  return 1
}

# ── error trap ───────────────────────────────────────────────────
CURRENT_STEP="initial setup"
on_error() {
  local ec="$1" line="$2"
  printf '\n'
  fail "Installation failed during: ${CURRENT_STEP}"
  hint "script line ${line} · exit code ${ec}"
  hint "the underlying error is shown just above"
  printf '\n  Need help? Open an issue:\n  %s%s%s\n\n' "$CYAN" "$ISSUES_URL" "$OFF"
}
trap 'on_error $? $LINENO' ERR

# ── args ─────────────────────────────────────────────────────────
usage() {
cat <<EOF

  ${BOLD}${CYAN}NEXUS${OFF} installer — Android (Termux)

  USAGE
    bash install-termux.sh [--help]

  WHAT IT DOES
    1. updates Termux packages and installs python + git
    2. verifies Python ${MIN_PY}+
    3. creates an isolated venv at ~/.nexus/venv
    4. installs Nexus in editable mode with extras: ${EXTRAS}
    5. puts the nexus command on your PATH (~/.profile + ~/.bashrc)
    6. runs a nexus doctor health check

  NOTES
    · tui + dashboard are the tested Termux target
    · voice extras are a stub today; browser automation
      (playwright) is not usable on stock Termux
    · re-running is safe (idempotent)
    · docs & issues: ${REPO_URL}

EOF
}

case "${1:-}" in
  -h|--help) usage; exit 0 ;;
  "")        ;;
  *) fail "Unknown option: $1"; hint "see: bash install-termux.sh --help"; exit 1 ;;
esac

# ── step 1: banner ───────────────────────────────────────────────
print_banner() {
  printf '\n%s' "$CYAN"
  printf '%s\n' \
    '   _  _ _____  ___   _ ___' \
    '  | \| | __\ \/ / | | / __|' \
    '  | .` | _| >  <| |_| \__ \' \
    '  |_| \_|___/_/\_\\___/|___/'
  printf '%s' "$OFF"
  printf '  %sself-hosted AI coding agent%s\n\n' "$DIM" "$OFF"
}
print_banner

# ── step 2: Termux base packages (idempotent) ────────────────────
if ! command -v pkg >/dev/null 2>&1; then
  fail "'pkg' not found — this does not look like Termux."
  hint "run this inside the Termux app, or on a laptop use:"
  hint "  bash install.sh          (auto-selects the laptop installer)"
  exit 1
fi

CURRENT_STEP="update Termux packages (pkg update)"
step "Updating Termux packages"
if ! pkg update -y; then
  warn "pkg update had mirror trouble — continuing anyway"
fi

CURRENT_STEP="install python & git (pkg install)"
step "Installing python and git"
pkg install -y python git
ok "Termux packages ready"

# ── step 3: verify Python >= MIN_PY ──────────────────────────────
CURRENT_STEP="verify Python ${MIN_PY}+"
if ! command -v python3 >/dev/null 2>&1; then
  fail "python3 is still missing after installation."
  hint "try:  pkg install python"
  hint "if that fails:  pkg update && pkg upgrade"
  exit 1
fi
PY_VER="$(python_version python3)" || PY_VER=""
if [ -z "$PY_VER" ] || ! ver_ge "$PY_VER" "$MIN_PY"; then
  fail "Nexus needs Python ${MIN_PY}+ — found: ${PY_VER:-unknown}"
  hint "upgrade your Termux Python:"
  hint "  pkg update && pkg upgrade python"
  exit 1
fi
ok "Python ${PY_VER}"

# ── step 4: venv at ~/.nexus/venv ────────────────────────────────
CURRENT_STEP="create virtual environment"
if [ -x "$VENV_DIR/bin/python" ]; then
  ok "Virtual environment already present"
else
  if [ -d "$VENV_DIR" ]; then
    warn "Found a broken venv — rebuilding it"
    rm -rf "$VENV_DIR"
  fi
  step "Creating isolated environment at ${VENV_DIR}"
  if ! python3 -m venv "$VENV_DIR"; then
    fail "python3 -m venv could not create the environment."
    hint "older Termux setups may need the pip bootstrap files:"
    hint "  pkg install python-pip python-virtualenv"
    hint "then re-run:  bash install-termux.sh"
    exit 1
  fi
  ok "Environment created"
fi

# ── step 5: pip install ──────────────────────────────────────────
CURRENT_STEP="upgrade pip"
step "Upgrading pip"
if ! "$VENV_DIR/bin/pip" install --upgrade pip --quiet >/dev/null 2>&1; then
  warn "pip upgrade skipped — continuing with the bundled version"
fi

CURRENT_STEP="install Nexus (pip install -e .[${EXTRAS}])"
step "Installing Nexus (extras: ${EXTRAS})"
if [ ! -f "$SCRIPT_DIR/pyproject.toml" ]; then
  fail "pyproject.toml not found next to this script (${SCRIPT_DIR})."
  hint "clone the full repository and install from inside it:"
  hint "  git clone ${REPO_URL} && cd Nexus && bash install.sh"
  exit 1
fi
if ! "$VENV_DIR/bin/pip" install -e ".[${EXTRAS}]"; then
  fail "Could not install Nexus from ${SCRIPT_DIR}."
  hint "check internet connection and free storage (~500 MB), then re-run:"
  hint "  bash install-termux.sh"
  exit 1
fi
ok "Nexus installed"

# ── step 6: PATH (grep-guarded, idempotent) ──────────────────────
CURRENT_STEP="configure PATH"
readonly PATH_LINE='export PATH="$HOME/.nexus/venv:$PATH"'
ensure_rc_line() {
  local f="$1"
  if [ ! -f "$f" ]; then : > "$f"; fi
  if ! grep -qxF -- "$PATH_LINE" "$f"; then
    printf '\n# Nexus (self-hosted AI agent) venv\n%s\n' "$PATH_LINE" >> "$f"
    ok "PATH updated in $f"
  else
    ok "PATH already configured in $f"
  fi
}
ensure_rc_line "$HOME/.profile"
ensure_rc_line "$HOME/.bashrc"

# ── step 7: nexus doctor (best-effort) ───────────────────────────
CURRENT_STEP="health check (nexus doctor)"
NEXUS_BIN="$VENV_DIR/bin/nexus"
if [ -x "$NEXUS_BIN" ]; then
  step "Running diagnostics (nexus doctor)"
  if "$NEXUS_BIN" doctor >/dev/null 2>&1; then
    ok "Doctor reports all systems nominal"
  else
    warn "nexus doctor reported issues (non-blocking)"
    hint "review them later with:  nexus doctor"
  fi
else
  warn "nexus command not found in the venv — skipping diagnostics"
fi

# ── step 8: success card ─────────────────────────────────────────
EXTRA_TIPS=()
case ":$PATH:" in
  *":$HOME/.nexus/venv:"*) : ;;
  *) EXTRA_TIPS+=("open a new session (or: source ~/.bashrc)") ;;
esac
if command -v termux-wake-lock >/dev/null 2>&1; then
  EXTRA_TIPS+=("termux-wake-lock keeps long agent runs alive")
fi

print_success_card() {
  local t
  printf '\n'
  printf '  %s────────────────────────────────────%s\n' "$CYAN" "$OFF"
  printf '  %s✦  NEXUS IS READY%s\n' "$BOLD$CYAN" "$OFF"
  printf '  %s────────────────────────────────────%s\n\n' "$CYAN" "$OFF"
  printf '  %sNext steps%s\n\n' "$BOLD" "$OFF"
  printf '    %s1.%s nexus auth        %sconnect a free provider%s\n'      "$GREEN" "$OFF" "$DIM" "$OFF"
  printf '    %s2.%s nexus tui         %sthe sci-fi terminal UI%s\n'     "$GREEN" "$OFF" "$DIM" "$OFF"
  printf '    %s3.%s nexus dashboard   %sweb UI: agent + telemetry%s\n'   "$GREEN" "$OFF" "$DIM" "$OFF"
  if [ "${#EXTRA_TIPS[@]}" -gt 0 ]; then
    printf '\n  %sNotes%s\n\n' "$BOLD" "$OFF"
    for t in "${EXTRA_TIPS[@]}"; do
      printf '    %s·%s %s\n' "$DIM" "$OFF" "$t"
    done
  fi
  printf '\n  %sDocs & issues:%s %s%s%s\n\n' "$BOLD" "$OFF" "$CYAN" "$REPO_URL" "$OFF"
}
print_success_card
