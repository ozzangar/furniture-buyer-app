# Requirements — Furniture Buyer App

What the app needs to do, in plain English. Grouped by level; each level builds on the last. A simple working
version of each item is enough — polish is for leftover time.

## Level 1 — a normal web app
- [ ] **Entity model** — the "things" the app remembers (Customer, Product, Order) and how they relate.
- [ ] **Web UI** — pages a user opens in a browser: a catalogue/home page and a product view.
- [ ] **User login** — tell one user apart from another; a logged-in user has a session.
- [ ] **Database** — users and orders persist across restarts.
- [ ] **Workflow / controller logic** — real rules, e.g. can't place an order that exceeds the remaining budget.
- [ ] **Reports** — a page summarising a user's own orders and total spent.
- [ ] **Internet-accessible** — the running app is reachable from another network via a public URL (tunnel).

## Level 2 — talk to the outside world
- [ ] **Shop catalogue** — list/browse real products from the shop API (search endpoint, not the heavy one).
- [ ] **Shop balance** — show the logged-in user's real balance from the shop API.
- [ ] **Shop orders** — place a real order via the shop API; show confirmation + updated balance.
- [ ] **Graceful errors** — insufficient balance, unknown item, rate limiting → clear user-facing messages.
- [ ] **Other external APIs** (pick what fits the demo): send an email, create a calendar invite (.ics),
      transcribe audio, call an LLM to generate/summarise text, receive a webhook.
- [ ] **Basic context engineering** when calling the LLM (feed it only what it needs).

## Level 3 — a natural-language agent
- [ ] A text box where a logged-in user types a plain-English request ("find me a chair under $500").
- [ ] The agent maps requests to the shop's four actions: **search catalogue, product detail, check balance,
      place order** — deciding which to call, in what order.
- [ ] Reasoning the API can't do (budget, colour, "cheap") happens in the model over the results.
- [ ] **Confirms before placing an order** (real spend); recovers gracefully from failures via chat.

## Level 4 — stretch
- [ ] A vector-RAG Q&A bot answering open-ended catalogue questions ("most affordable option in blue?").

## Non-functional
- Secrets in `.env` only (git-ignored). Beginner-friendly, readable code. Small, frequent commits.
