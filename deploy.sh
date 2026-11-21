#!/usr/bin/env bash
# Обновить бота на сервере (сначала закоммить изменения):
#   ./deploy.sh              — полный деплой: pull, зависимости, перезапуск (идущие задачи студентов оборвутся)
#   ./deploy.sh --templates  — только шаблоны: pull и перечитывание банка без перезапуска (сигнал SIGHUP)
set -euo pipefail
cd "$(dirname "$0")"

# адрес сервера и ключ — из .env (DEPLOY_HOST, DEPLOY_KEY), чтобы не светить их в публичном репозитории
env_get() { { grep -E "^$1=" .env 2>/dev/null || true; } | head -1 | cut -d= -f2- | sed 's/[[:space:]]*#.*$//; s/^["'\'']//; s/["'\'']$//'; }
HOST="${DEPLOY_HOST:-$(env_get DEPLOY_HOST)}"
KEY="${DEPLOY_KEY:-$(env_get DEPLOY_KEY)}"
KEY="${KEY/#\~/$HOME}"
if [[ -z "$HOST" || -z "$KEY" ]]; then
  echo "Не заданы DEPLOY_HOST / DEPLOY_KEY — добавь их в .env (образец в .env.example)"; exit 1
fi

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
