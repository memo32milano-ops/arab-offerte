#!/usr/bin/env bash
# Keeps the bot alive for ~5h50m inside one GitHub Actions job (job limit is 6h).
# Checks Telegram every 60s, saves state.json to the repo every ~10 minutes.
set -u
git config user.name "deals-bot"
git config user.email "deals-bot@users.noreply.github.com"
END=$(( $(date +%s) + 5*3600 + 50*60 ))
i=0
save() {
  git add state.json
  if ! git diff --cached --quiet; then
    git commit -m "bot state" -q
    git pull --rebase -q || true
    git push -q || true
  fi
}
while [ "$(date +%s)" -lt "$END" ]; do
  python bot.py || echo "bot run failed (will retry)"
  i=$((i+1))
  if [ $((i % 10)) -eq 0 ]; then save; fi
  sleep 60
done
save
