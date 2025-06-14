#!/usr/bin/env bash

set -euo pipefail
sudo apt-get update && sudo apt-get install -y \
  direnv
# install uv
curl -LsSf https://astral.sh/uv/install.sh | sh
uv venv --python=python3.12
uv sync
for f in ./*.default ./.*.default; do
  dest="${f%.default}"
  if [[ -f "$f" ]]; then
    cp -v "$f" "$dest"
    echo "Don't forget to add your own values in $dest"
  fi
done
