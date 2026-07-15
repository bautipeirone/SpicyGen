#!/bin/sh
set -eu

export DEBIAN_FRONTEND=noninteractive

apt-get update
# shellcheck disable=SC2086
apt-get install -y --no-install-recommends bash jq ${EXTRA_APT_PACKAGES:-}
rm -rf /var/lib/apt/lists/*

if ! command -v python >/dev/null 2>&1 && command -v python3 >/dev/null 2>&1; then
  ln -sf /usr/bin/python3 /usr/local/bin/python
fi

PIP_BIN="${PIP_BIN:-pip}"
if ! command -v "$PIP_BIN" >/dev/null 2>&1 && command -v pip3 >/dev/null 2>&1; then
  PIP_BIN=pip3
fi

if ! command -v uv >/dev/null 2>&1; then
  # shellcheck disable=SC2086
  "$PIP_BIN" install ${PIP_INSTALL_ARGS:-} --no-cache-dir uv
fi

uv sync --locked --no-dev --no-install-project
