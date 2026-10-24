#!/usr/bin/env bash
# Azilal generator service: auto-update from GitHub, then (re)start on port 7860.
cd "$(dirname "$0")/.." || exit 1

# 1) Always pull the latest code first (best effort, offline-safe)
git pull --ff-only 2>/dev/null || true

# 2) LibreOffice on first run only
if ! command -v soffice >/dev/null 2>&1; then
  sudo apt-get update -qq
  sudo apt-get install -y libreoffice-calc fonts-noto-core fonts-dejavu-core
fi

# 3) Restart the service so it always runs the pulled code
if pgrep -f "python3 app.py" >/dev/null 2>&1; then
  pkill -f "python3 app.py" 2>/dev/null
  sleep 1
fi
PORT=7860 nohup python3 app.py > /tmp/azilal-server.log 2>&1 &
echo "Azilal service up on port 7860 @ $(git rev-parse --short HEAD 2>/dev/null)"

# 4) Keep port 7860 PUBLIC on every start (API starts reset visibility to private)
if command -v gh >/dev/null 2>&1; then
  if [ -n "${CODESPACE_NAME:-}" ]; then
    gh codespace ports visibility 7860:public -c "$CODESPACE_NAME" 2>/dev/null || true
  else
    gh codespace ports visibility 7860:public 2>/dev/null || true
  fi
fi
