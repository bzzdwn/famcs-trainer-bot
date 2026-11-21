#!/usr/bin/env bash
# Обновить бота на сервере: ./deploy.sh  (сначала закоммить и запушь изменения)
set -euo pipefail
HOST="${MATAN_HOST:-root@89.124.76.119}"
KEY="${MATAN_KEY:-$HOME/.ssh/matan_bot}"

git push
ssh -i "$KEY" "$HOST" '
  set -e
  su - bot -c "cd ~/app && git pull --ff-only && venv/bin/pip install -q -r requirements.txt"
  systemctl restart matan-bot
  sleep 5
  systemctl is-active matan-bot
  journalctl -u matan-bot --no-pager -n 5 -o cat
'
