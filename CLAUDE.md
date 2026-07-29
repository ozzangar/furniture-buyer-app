# CLAUDE.md — Furniture Buyer App

Standing instructions for Claude Code in this repo. Read automatically at the start of every session.

## What this is
A **buyer's web app** for a furniture shop, built for a hands-on AI-coding lab. A user logs in, browses a
product catalogue, and places orders against a per-user budget. Built in stages ("levels"):

1. **Level 1** — web UI + login + database + basic reports; reachable over the internet.
2. **Level 2** — call the shop's external API (catalogue, balance, orders) + other APIs (email, calendar, etc.).
3. **Level 3** — a natural-language agent that decides which shop actions to take from a plain-English request.
4. **Level 4 (stretch)** — a vector-RAG product Q&A bot.

The shop's catalogue/balance/order API is an **external service** with its own auth (an API key). This app is
the buyer side only.

## How to work in this repo
- **Describe the goal, not the file-by-file implementation.** Keep changes small and reviewable.
- **Run and look at it** after each change — don't assume "it should work."
- Prefer a simple, beginner-friendly stack; explain non-obvious tech choices in one line.
- **Plan before big changes:** keep `requirements.md` (what it must do) and `architecture.md` (how it's built) current.

## Hard rules
- **Never commit secrets.** The shop API key and any connection strings live in `.env` (git-ignored). Never
  hardcode them, never paste them into a file that gets committed, never put them in a commit message.
- **Confirm before spending.** `POST /orders` on the shop API debits a real (event) balance — any code path
  that places an order must be deliberate and, for the agent, confirmed with the user first.
- **Handle API errors gracefully** — show clear messages for insufficient balance / unknown item / rate limits,
  never a raw stack trace or blank page.
- Commit at each working milestone with a plain-English message.

## Conventions
- Secrets/config via environment variables read from `.env`; keep a `.env.example` with placeholder keys committed.
- Keep the entity model (Customer / Product / Order) in `architecture.md` in sync with the code.
