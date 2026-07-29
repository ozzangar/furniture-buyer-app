# Furniture Buyer App

A buyer's web application for a furniture shop, built during an AI-assisted coding lab. Users log in, browse a
product catalogue, and place orders against a budget — starting as a plain web app and growing into a
natural-language shopping agent.

## Status
🟢 Level 1 working: catalogue, register/login, place orders against a budget (overspend blocked),
per-user order history + total spent. Catalogue is placeholder data (real shop API arrives in Step 5).
See [`requirements.md`](requirements.md) for the checklist and [`architecture.md`](architecture.md) for the design.

## Getting started
```bash
python3 -m venv --symlinks .venv          # system python (see note below)
./.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env                       # fill in real values later (never commit .env)
FLASK_SECRET_KEY=dev ./.venv/bin/python app.py
```
Then open http://127.0.0.1:5000 — register an account, browse, and place an order.
The SQLite database is created automatically at `data/app.db` on first run.

> **Note (macOS system Python / LibreSSL):** password hashing uses `pbkdf2:sha256` because Apple's
> system Python lacks `hashlib.scrypt` (Werkzeug's default). No action needed — just don't switch to `scrypt`.

## Project layout
- `CLAUDE.md` — standing instructions for AI-assisted development
- `requirements.md` — what the app must do (by level)
- `architecture.md` — stack, entity model (Mermaid), and how the pieces talk
- `.env.example` — template for local config/secrets (real `.env` is git-ignored)
