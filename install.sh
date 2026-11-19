#!/usr/bin/env bash
#
# Nexus — platform-dispatch installer
#
# Detects the environment (Termux/Android vs laptop) and hands off to the
# right installer. Kept intentionally tiny: all logic lives in
# install-termux.sh and install-laptop.sh.
#
# Docs: https://github.com/alpha-1-design/Nexus

set -eo pipefail

# Resolve the directory this script lives in, so it works no matter
# where it is invoked from (e.g. `bash /path/to/install.sh --help`,
# `nexus upgrade` inside nexus/cli/commands.py, or ./install.sh).
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]:-$0}")" >/dev/null 2>&1 && pwd -P)"

CYAN='' OFF=''
if [ -t 1 ] && [ "${TERM:-}" != "dumb" ] && [ -z "${NO_COLOR:-}" ]; then
  CYAN=$'\033[36m'
  BOLD=$'\033[1m'
  OFF=$'\033[0m'
fi

usage() {
cat <<EOF

  ${BOLD}${CYAN}NEXUS${OFF} installer — self-hosted AI coding agent

  USAGE
    bash install.sh [OPTIONS]

  This script detects your platform and runs the right installer:

    Android (Termux)   →  install-termux.sh
    Linux / macOS      →  install-laptop.sh

  ${BOLD}TERMUX INSTALLER${OFF}
    bash install.sh              (no options needed on Android)
    More info: bash install-termux.sh --help

  ${BOLD}LAPTOP INSTALLER${OFF}
    bash install.sh --with-browser    also install Playwright + Chromium
    bash install.sh --dev             also install dev/test extras
    bash install.sh --uninstall       remove Nexus from this machine
    More info: bash install-laptop.sh --help

  ENVIRONMENT
    NO_COLOR=1    disable colored output

  Both installers are idempotent — safe to run again at any time.
  Docs & issues: https://github.com/alpha-1-design/Nexus

EOF
}

is_termux() {
  # 1) Official Termux builds export TERMUX_VERSION.
  [ -n "${TERMUX_VERSION:-}" ] && return 0
  # 2) $PREFIX points into the Termux app sandbox.
  case "${PREFIX:-}" in *com.termux*) return 0 ;; esac
  # 3) The Android app data directory exists.
  [ -d /data/data/com.termux ] && return 0
  return 1
}

case "${1:-}" in
  -h|--help) usage; exit 0 ;;
esac

if [ "$#" -gt 0 ]; then
  # Flags are forwarded verbatim to the platform installer.
  if is_termux; then
    TARGET="$SCRIPT_DIR/install-termux.sh"
  else
    TARGET="$SCRIPT_DIR/install-laptop.sh"
  fi
  if [ ! -f "$TARGET" ]; then
    printf 'install.sh: cannot find %s — run this from a full Nexus checkout.\n' "$(basename "$TARGET")" >&2
    exit 1
  fi
  exec bash "$TARGET" "$@"
fi

# No flags: plain dispatch (exec replaces this process).
if is_termux; then
  exec bash "$SCRIPT_DIR/install-termux.sh"
else
  exec bash "$SCRIPT_DIR/install-laptop.sh"
fi
