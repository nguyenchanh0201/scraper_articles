#!/bin/sh
set -eu

: "${API_KEY:?API_KEY is required}"
: "${GITHUB_TOKEN:?GITHUB_TOKEN is required}"
: "${GITHUB_REPO:?GITHUB_REPO is required, for example nguyenchanh0201/scraper_articles}"

GITHUB_BRANCH="${GITHUB_BRANCH:-master}"
WORK_DIR="/tmp/scraper_articles_repo"
REPO_URL="https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPO}.git"

rm -rf "$WORK_DIR"
git clone --branch "$GITHUB_BRANCH" "$REPO_URL" "$WORK_DIR"

cd "$WORK_DIR"
python /app/main.py

git config user.name "railway-cron-bot"
git config user.email "railway-cron-bot@users.noreply.github.com"

git add scraped_articles
if git diff --cached --quiet; then
  echo "No scraped article changes to commit."
else
  git commit -m "Update scraped articles"
  git push origin "$GITHUB_BRANCH"
fi
