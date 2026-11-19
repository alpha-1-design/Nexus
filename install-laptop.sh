#!/usr/bin/env bash
#
# Nexus — laptop installer (Linux / macOS)
#
# Installs Nexus into an isolated venv at ~/.nexus/venv with the
# tui + dashboard extras, links the nexus command into ~/.local/bin
# and runs a health check.
#
# No system packages are ever installed silently: if Python is missing
# you get per-OS hints instead. Safe to run again: every step is idempotent.
#
# Docs & issues: https://github.com/alpha-1-design/Nexus/issues

set -euo pipefail

readonly NEXUS_DIR="${NEXUS_DIR:-$HOME/.nexus}"
readonly VENV_DIR="$NEXUS_DIR/venv"
readonly REPO_URL="https://github.com/alpha-1-design/Nexus"
readonly ISSUES_URL="https://github.com/alpha-1-design/Nexus/issues"
readonly MIN_PY="3.10"

# Directory this script lives in — the Nexus checkout to install from.
# Resolved robustly so `bash /path/to/install-laptop.sh` works anywhere.
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

# ── version helpers (keep identical to install-termux.sh) ────────
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
WITH_BROWSER=0
WITH_DEV=0
UNINSTALL=0

usage() {
cat <<EOF

  ${BOLD}${CYAN}NEXUS${OFF} installer — Linux / macOS laptop

  USAGE
    bash install-laptop.sh [OPTIONS]

  OPTIONS
    --with-browser    also install the browser extra and Chromium
                      (Playwright — best effort, soft-fails offline)
    --dev             also install dev/test extras (pytest, ruff, mypy)
                      if pyproject.toml defines a 'dev' extra
    --uninstall       remove the venv, the nexus command and the
                      PATH lines added by this installer
    -h, --help        show this help

  WHAT IT DOES
    1. verifies Python ${MIN_PY}+ (prints per-OS install hints if missing)
    2. creates an isolated venv at ~/.nexus/venv
    3. installs Nexus in editable mode with extras: tui, dashboard
    4. links the nexus command into ~/.local/bin
    5. runs a nexus doctor health check

  NOTES
    · never installs system packages or uses sudo
    · re-running is safe (idempotent)
    · docs & issues: ${REPO_URL}

EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --with-browser) WITH_BROWSER=1 ;;
    --dev)          WITH_DEV=1 ;;
    --uninstall)    UNINSTALL=1 ;;
    -h|--help)      usage; exit 0 ;;
    *) fail "Unknown option: $1"; hint "see: bash install-laptop.sh --help"; exit 1 ;;
  esac
  shift
done

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

# Source checkout sanity check (needed by every mode below).
if [ ! -f "$SCRIPT_DIR/pyproject.toml" ]; then
  fail "pyproject.toml not found next to this script."
  hint "clone the full repository and install from inside it:"
  hint "  git clone ${REPO_URL} && cd Nexus && bash install.sh"
  exit 1
fi

# ── --uninstall: clean exit path ─────────────────────────────────
readonly PATH_LINE='export PATH="$HOME/.nexus/venv:$PATH"'
if [ "$UNINSTALL" = "1" ]; then
  CURRENT_STEP="uninstall"
  step "Removing Nexus from this machine"
  if [ -d "$VENV_DIR" ]; then
    rm -rf "$VENV_DIR"
    ok "Removed $VENV_DIR"
  else
    ok "No venv found (already clean)"
  fi
  if [ -e "$HOME/.local/bin/nexus" ] || [ -L "$HOME/.local/bin/nexus" ]; then
    rm -f "$HOME/.local/bin/nexus"
    ok "Removed the nexus command"
  else
    ok "No nexus command found"
  fi
  local_rc=""
  for rc in "$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.profile"; do
    if [ -f "$rc" ] && grep -qF -- "$PATH_LINE" "$rc"; then
      local_rc="$(mktemp)"
      grep -vF -e "$PATH_LINE" -e '# Nexus (self-hosted AI agent) venv' "$rc" > "$local_rc" || true
      mv "$local_rc" "$rc"
      ok "PATH line removed from $rc"
    fi
  done
  printf '\n  Nexus is uninstalled. Your data (config, sessions,\n  skills at ~/.nexus) was kept — delete ~/.nexus manually\n  for a full wipe.\n\n'
  exit 0
fi

# ── step 2: verify Python >= MIN_PY ──────────────────────────────
CURRENT_STEP="verify Python ${MIN_PY}+"
PY_BIN=""
PY_VER=""
OLD_PY_VER=""
find_python() {
  local cand ver
  for cand in python3 python; do
    command -v "$cand" >/dev/null 2>&1 || continue
    ver="$(python_version "$cand")" || continue
    if ver_ge "$ver" "$MIN_PY"; then
      PY_BIN="$cand"
      PY_VER="$ver"
      return 0
    fi
    if [ -z "$OLD_PY_VER" ] || ver_ge "$ver" "$OLD_PY_VER"; then
      OLD_PY_VER="$ver"
    fi
  done
  return 1
}
if find_python; then
  ok "Python ${PY_VER} (${PY_BIN})"
else
  fail "Nexus needs Python ${MIN_PY}+ — found: ${OLD_PY_VER:-none}"
  hint "Debian/Ubuntu:  sudo apt install python3 python3-venv"
  hint "Fedora:         sudo dnf install python3"
  hint "Arch:           sudo pacman -S python"
  hint "macOS (brew):   brew install python"
  exit 1
fi

# ── step 3: venv at ~/.nexus/venv ────────────────────────────────
CURRENT_STEP="create virtual environment"
if [ -x "$VENV_DIR/bin/python" ]; then
  ok "Virtual environment already present"
else
  if [ -d "$VENV_DIR" ]; then
    warn "Found a broken venv — rebuilding it"
    rm -rf "$VENV_DIR"
  fi
  step "Creating isolated environment at ${VENV_DIR}"
  if ! "$PY_BIN" -m venv "$VENV_DIR"; then
    fail "python -m venv could not create the environment."
    hint "Debian/Ubuntu needs the venv module packaged separately:"
    hint "  sudo apt install python3-venv"
    hint "then re-run:  bash install-laptop.sh"
    exit 1
  fi
  ok "Environment created"
fi

# ── step 4: extras + pip install ─────────────────────────────────
EXTRAS="tui,dashboard"
if [ "$WITH_BROWSER" = "1" ]; then EXTRAS="browser,$EXTRAS"; fi
if [ "$WITH_DEV" = "1" ]; then
  if awk '/^[[:space:]]*\[/{ inopt = ($0 ~ /\[project\.optional-dependencies\]/) }
          inopt && /^[[:space:]]*dev[[:space:]]*=/ { found = 1 }
          END { exit !found }' "$SCRIPT_DIR/pyproject.toml"; then
    EXTRAS="$EXTRAS,dev"
  else
    warn "pyproject.toml defines no 'dev' extra — skipping --dev"
  fi
fi

CURRENT_STEP="upgrade pip"
step "Upgrading pip"
if ! "$VENV_DIR/bin/pip" install --upgrade pip --quiet >/dev/null 2>&1; then
  warn "pip upgrade skipped — continuing with the bundled version"
fi

CURRENT_STEP="install Nexus (pip install -e .[${EXTRAS}])"
step "Installing Nexus (extras: ${EXTRAS})"
if ! "$VENV_DIR/bin/pip" install -e ".[${EXTRAS}]"; then
  fail "Could not install Nexus from ${SCRIPT_DIR}."
  hint "check your internet connection, then re-run:"
  hint "  bash install-laptop.sh"
  exit 1
fi
ok "Nexus installed"

# ── step 4b: browser extra — Chromium download (best effort) ─────
if [ "$WITH_BROWSER" = "1" ]; then
  CURRENT_STEP="download Chromium (playwright install)"
  step "Downloading Chromium for browser automation"
  if "$VENV_DIR/bin/python" -m playwright install chromium; then
    ok "Chromium ready"
  else
    warn "Chromium download failed — Nexus still works without it"
    hint "retry later:  ~/.nexus/venv/bin/playwright install chromium"
    hint "until then, browser tools are simply unavailable"
  fi
fi

# ── step 5: ~/.local/bin symlink + PATH ──────────────────────────
CURRENT_STEP="configure PATH"
LOCAL_BIN="$HOME/.local/bin"
if [ ! -d "$LOCAL_BIN" ]; then
  mkdir -p "$LOCAL_BIN"
  ok "Created $LOCAL_BIN"
fi
step "Linking the nexus command"
if [ -x "$VENV_DIR/bin/nexus" ]; then
  ln -sfn "$VENV_DIR/bin/nexus" "$LOCAL_BIN/nexus"
  ok "Linked $LOCAL_BIN/nexus"
else
  warn "venv has no nexus entry point — the link was skipped"
fi

IN_PATH=0
case ":$PATH:" in
  *":$LOCAL_BIN:"*) IN_PATH=1 ;;
esac

SHELL_NAME="$(basename "${SHELL:-bash}")"
RC_FILE=""
case "$SHELL_NAME" in
  zsh)  RC_FILE="$HOME/.zshrc" ;;
  fish) warn "fish shell detected — add $LOCAL_BIN to your PATH manually" ;;
  *)    RC_FILE="$HOME/.bashrc"
        if [ "$SHELL_NAME" != "bash" ]; then
          warn "unknown shell '$SHELL_NAME' — defaulting to ~/.bashrc"
        fi ;;
esac

RC_ADDED=0
if [ -n "$RC_FILE" ]; then
  if [ ! -f "$RC_FILE" ]; then : > "$RC_FILE"; fi
  if ! grep -q -- '\.local/bin' "$RC_FILE"; then
    printf '\n# Nexus (self-hosted AI agent)\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$RC_FILE"
    RC_ADDED=1
    ok "PATH updated in $RC_FILE"
  else
    ok "PATH already configured in $RC_FILE"
  fi
fi

# ── step 6: nexus doctor (best-effort) ───────────────────────────
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

# ── step 7: success card ─────────────────────────────────────────
EXTRA_TIPS=()
if [ "$IN_PATH" = "0" ]; then
  if [ "$RC_ADDED" = "1" ]; then
    EXTRA_TIPS+=("open a new terminal (or: source ~/.${RC_FILE##*/})")
  else
    EXTRA_TIPS+=("open a new terminal to use 'nexus'")
  fi
fi
EXTRA_TIPS+=("API keys live in ~/.nexus/.env (auto-loaded)")

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
