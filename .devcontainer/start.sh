#!/usr/bin/env bash
# Runs on every Codespace start: install LibreOffice once, then serve.
set -e
if ! command -v soffice >/dev/null 2>&1; then
  echo "Installing LibreOffice (first start only)..."
  sudo apt-get update
  sudo apt-get install -y --no-install-recommends \
    libreoffice-calc fonts-noto-naskh-arabic fonts-dejavu-core
fi
cd "$(dirname "$0")/.."
if ! pgrep -f "python3 app.py" >/dev/null 2>&1; then
  PORT=7860 nohup python3 app.py > /tmp/azilal-server.log 2>&1 &
  echo "Azilal generator starting on port 7860 (log: /tmp/azilal-server.log)"
else
  echo "Azilal generator already running"
fi
