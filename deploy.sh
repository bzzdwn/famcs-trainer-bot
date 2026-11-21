#!/usr/bin/env bash
# Обновить бота на сервере (сначала закоммить изменения):
#   ./deploy.sh              — полный деплой: pull, зависимости, перезапуск (идущие задачи студентов оборвутся)
#   ./deploy.sh --templates  — только шаблоны: pull и перечитывание банка без перезапуска (сигнал SIGHUP)
set -euo pipefail
HOST="${MATAN_HOST:-root@89.124.76.119}"
KEY="${MATAN_KEY:-$HOME/.ssh/matan_bot}"

if [[ "${1:-}" == "--templates" ]]; then
  git fetch -q origin
  OTHER=$(git diff --name-only origin/main..HEAD | grep -v '^data/templates/' || true)
  if [[ -n "$OTHER" ]]; then
    echo "Изменены не только шаблоны — нужен полный ./deploy.sh:"; echo "$OTHER"; exit 1
  fi
  git push
  ssh -i "$KEY" "$HOST" '
    set -e
    su - bot -c "cd ~/app && git pull --ff-only"
    systemctl kill -s HUP --kill-whom=main matan-bot
    echo "Шаблоны перечитываются — результат придёт в Telegram"
  '
  exit 0
fi

git push
ssh -i "$KEY" "$HOST" '
  set -e
  su - bot -c "cd ~/app && git pull --ff-only && venv/bin/pip install -q -r requirements.lock"
  systemctl restart matan-bot
  sleep 5
  systemctl is-active matan-bot
  journalctl -u matan-bot --no-pager -n 5 -o cat
'
