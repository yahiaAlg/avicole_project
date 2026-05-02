#!/usr/bin/env bash
# build.sh — Render deploy script
# Runs once on every deploy (not on every dyno start).
set -o errexit   # exit immediately on any error

echo "==> Installing Python dependencies"
pip install -r requirements.txt

echo "==> Running database migrations"
python manage.py migrate --noinput

echo "==> Collecting static files"
python manage.py collectstatic --noinput --clear

echo "==> Seeding the database"
python manage.py seed_db --mode demo

echo "==> Build complete"