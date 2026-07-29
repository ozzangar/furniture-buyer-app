"""Furniture Buyer App — Level 1.

A small Flask app: browse a catalogue, log in, place orders against a budget,
and see a report of your own orders. Catalogue data is placeholder for now
(Step 5 swaps in the real shop API). Own data (users, orders) lives in SQLite.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone

from flask import (
    Flask, g, redirect, render_template, request, session, url_for, flash, abort,
)
from werkzeug.security import check_password_hash, generate_password_hash

import shop_api

# Load .env (SHOP_API_* and FLASK_SECRET_KEY) if python-dotenv is available.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except Exception:
    pass

# --- config -----------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data", "app.db")

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-only-not-for-production")

# Password hashing: system python is built on LibreSSL, which lacks hashlib.scrypt
# (Werkzeug's default). pbkdf2:sha256 works everywhere.
PW_METHOD = "pbkdf2:sha256"


# --- database helpers --------------------------------------------------------
def get_db() -> sqlite3.Connection:
    if "db" not in g:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_exc: object | None = None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db() -> None:
    db = get_db()
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS customer (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            display_name  TEXT NOT NULL,
            created_at    TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS "order" (
            id           TEXT PRIMARY KEY,
            customer_id  INTEGER NOT NULL REFERENCES customer(id),
            placed_at    TEXT NOT NULL,
            total_price  REAL NOT NULL,
            status       TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS order_line (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id    TEXT NOT NULL REFERENCES "order"(id),
            item_id     TEXT NOT NULL,
            product_name TEXT NOT NULL,
            quantity    INTEGER NOT NULL,
            unit_price  REAL NOT NULL
        );
        """
    )
    db.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- auth --------------------------------------------------------------------
def current_user() -> sqlite3.Row | None:
    uid = session.get("user_id")
    if uid is None:
        return None
    return get_db().execute("SELECT * FROM customer WHERE id = ?", (uid,)).fetchone()


def live_balance() -> float | None:
    """Current balance from the shop API, or None if it can't be read."""
    try:
        return shop_api.get_balance().get("balance")
    except shop_api.ShopAPIError:
        return None


@app.context_processor
def inject_user() -> dict:
    user = current_user()
    balance = live_balance() if user else None
    return {"user": user, "balance": balance}


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        display = (request.form.get("display_name") or "").strip() or username
        if not username or not password:
            flash("Username and password are required.", "error")
            return render_template("register.html")
        db = get_db()
        if db.execute("SELECT 1 FROM customer WHERE username = ?", (username,)).fetchone():
            flash("That username is taken.", "error")
            return render_template("register.html")
        db.execute(
            "INSERT INTO customer (username, password_hash, display_name, created_at)"
            " VALUES (?, ?, ?, ?)",
            (username, generate_password_hash(password, method=PW_METHOD), display, _now()),
        )
        db.commit()
        flash("Account created — please log in.", "ok")
        return redirect(url_for("login"))
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        row = get_db().execute(
            "SELECT * FROM customer WHERE username = ?", (username,)
        ).fetchone()
        if row and check_password_hash(row["password_hash"], password):
            session.clear()
            session["user_id"] = row["id"]
            return redirect(url_for("index"))
        flash("Invalid username or password.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


# --- catalogue + ordering (real shop API) ------------------------------------
CATALOGUE_PAGE = 24      # products per page (initial render + each infinite-scroll fetch)
CATALOGUE_TTL = 600.0    # seconds to cache the full catalogue (it's static during the event)

_catalogue_cache: dict = {"at": 0.0, "products": None}


def _all_products() -> list[dict]:
    """Full catalogue (all 762), cached in-process. One fast search-index call, no images."""
    import time
    now = time.time()
    if _catalogue_cache["products"] is None or (now - _catalogue_cache["at"]) > CATALOGUE_TTL:
        _catalogue_cache["products"] = shop_api.list_products(limit=1000)
        _catalogue_cache["at"] = now
    return _catalogue_cache["products"]


def _filter_products(category: str | None, query: str) -> list[dict]:
    """Filter the full catalogue by exact category and/or a name substring (case-insensitive)."""
    items = _all_products()
    if category:
        c = category.lower()
        items = [p for p in items if (p.get("category") or "").lower() == c]
    if query:
        q = query.lower()
        items = [p for p in items if q in (p.get("product_name") or "").lower()]
    return items


def _page(category: str | None, query: str, skip: int, limit: int):
    """A page over the FILTERED catalogue. Returns (page_items, has_more, total_matches).

    Paginating the filtered set means a search always shows matching products on page 1
    (no empty pages) and we know the exact total up front.
    """
    items = _filter_products(category, query)
    total = len(items)
    page = items[skip:skip + limit]
    for p in page:
        p["_image_url"] = shop_api.product_image_url(p["item_id"])
    return page, (skip + limit < total), total


@app.route("/")
def index():
    category = request.args.get("category") or None
    query = (request.args.get("q") or "").strip()
    try:
        products, has_more, total = _page(category, query, 0, CATALOGUE_PAGE)
        categories = shop_api.get_categories()
    except shop_api.ShopAPIError as e:
        flash(f"Catalogue unavailable: {e.message}", "error")
        products, categories, has_more, total = [], [], False, 0
    return render_template(
        "index.html", products=products, categories=categories, active_category=category,
        query=query, has_more=has_more, page_size=CATALOGUE_PAGE, total=total,
    )


@app.route("/api/products")
def api_products():
    """JSON page of products for infinite scroll (paginates over the filtered catalogue)."""
    category = request.args.get("category") or None
    query = (request.args.get("q") or "").strip()
    try:
        skip = max(0, int(request.args.get("skip", "0")))
    except (TypeError, ValueError):
        skip = 0
    try:
        products, has_more, total = _page(category, query, skip, CATALOGUE_PAGE)
    except shop_api.ShopAPIError as e:
        return {"error": e.message, "products": [], "has_more": False, "total": 0}, 200
    return {
        "products": [{"item_id": p["item_id"], "product_name": p["product_name"],
                      "price": p["price"], "category": p["category"],
                      "image_url": p["_image_url"]} for p in products],
        "next_skip": skip + CATALOGUE_PAGE,
        "has_more": has_more,
        "total": total,
    }


@app.route("/product/<item_id>")
def product(item_id: str):
    try:
        p = shop_api.get_product(item_id)
    except shop_api.ShopAPIError as e:
        flash(e.message, "error")
        return redirect(url_for("index"))
    if p is None:
        abort(404)
    p["_image_url"] = shop_api.product_image_url(item_id)
    return render_template("product.html", product=p)


@app.route("/buy/<item_id>", methods=["POST"])
def buy(item_id: str):
    if current_user() is None:
        flash("Please log in to place an order.", "error")
        return redirect(url_for("login"))
    try:
        qty = max(1, int(request.form.get("quantity", "1")))
    except ValueError:
        qty = 1
    try:
        result = shop_api.place_order(item_id, qty)
    except shop_api.ShopAPIError as e:
        # 402 insufficient / 404 missing / etc. — clear message, no crash.
        flash(e.message, "error")
        return redirect(url_for("product", item_id=item_id))
    total = result.get("total_price", 0.0)
    remaining = result.get("remaining_balance")
    msg = f"Order placed: {qty} × {item_id} for ${total:,.2f}."
    if remaining is not None:
        msg += f" Remaining balance: ${remaining:,.2f}."
    flash(msg, "ok")
    return redirect(url_for("orders"))


@app.route("/orders")
def orders():
    if current_user() is None:
        flash("Please log in to see your orders.", "error")
        return redirect(url_for("login"))
    try:
        history = shop_api.get_order_history()
    except shop_api.ShopAPIError as e:
        flash(e.message, "error")
        history = []
    # Normalise: API returns newest-last; show newest first.
    history = list(reversed(history))
    total_spent = sum(o.get("total_amount", o.get("total_price", 0.0)) for o in history)
    return render_template("orders.html", orders=history, total_spent=total_spent)


import threading

import shop_agent

MAX_ORDER_VALUE = 3000.0  # must match shop_agent; enforced again here at execute time

# One-time-use tokens for staged orders + a lock, so a confirm executes at most once
# even under concurrent requests (defends against the double-spend race).
_ORDER_LOCK = threading.Lock()
_UNCLAIMED_ORDER_TOKENS: set[str] = set()


@app.route("/agent")
def agent_page():
    # The assistant is now a floating widget on every page (see _agent_widget.html).
    # Keep this route as a gentle fallback: send logged-in users home where the FAB lives.
    if current_user() is None:
        flash("Please log in to use the shopping assistant.", "error")
        return redirect(url_for("login"))
    flash("Tap the ✨ button, bottom-right, to chat with the shopping assistant.", "ok")
    return redirect(url_for("index"))


@app.route("/agent/message", methods=["POST"])
def agent_message():
    if current_user() is None:
        return {"error": "not logged in"}, 401
    text = (request.get_json(silent=True) or {}).get("message", "")
    history = session.get("agent_history", [])
    try:
        result = shop_agent.run_turn(history, text)
    except Exception as e:  # never leak a stack trace to the user
        app.logger.exception("agent turn failed")
        return {"reply": f"The assistant hit an error: {type(e).__name__}. Try again.",
                "pending": None}, 200
    session["agent_history"] = result["history"][-20:]  # bound stored context
    pending = result["pending"]
    if pending:
        # Stash the staged order server-side with a one-time token. The token — not the
        # order details — is what /agent/confirm must claim, so a purchase executes at
        # most once even under concurrent confirms (see _claim_order).
        import uuid
        token = uuid.uuid4().hex
        session["pending_order"] = {"item_id": pending["item_id"],
                                    "quantity": pending["quantity"], "token": token}
        with _ORDER_LOCK:
            _UNCLAIMED_ORDER_TOKENS.add(token)
    else:
        old = session.pop("pending_order", None)
        if old:
            with _ORDER_LOCK:
                _UNCLAIMED_ORDER_TOKENS.discard(old.get("token"))
    return {"reply": result["reply"], "pending": pending,
            "products": result.get("products", [])}


@app.route("/agent/confirm", methods=["POST"])
def agent_confirm():
    """The ONLY place a real purchase executes. Physical gate: requires a human
    request + a server-side staged order; re-fetches price and re-checks the cap.
    The LLM has no path to trigger this route."""
    if current_user() is None:
        return {"error": "not logged in"}, 401
    staged = session.get("pending_order")
    if not staged:
        return {"reply": "There's no order waiting to confirm.", "pending": None}, 200
    item_id, qty = staged["item_id"], staged["quantity"]
    token = staged.get("token")
    session.pop("pending_order", None)  # clear from this request's session copy

    # Atomically CLAIM the one-time token. Concurrent confirms race here; only the
    # first to remove the token proceeds — the rest see it already claimed. This is
    # what makes the purchase execute at most once regardless of concurrency.
    with _ORDER_LOCK:
        if token not in _UNCLAIMED_ORDER_TOKENS:
            return {"reply": "That order was already confirmed.", "pending": None}, 200
        _UNCLAIMED_ORDER_TOKENS.discard(token)

    # Re-verify server-side — don't trust anything the model said earlier.
    p = shop_api.get_product(item_id)
    if p is None:
        return {"reply": "That item is no longer available.", "pending": None}, 200
    total = round(p["price"] * qty, 2)
    if total > MAX_ORDER_VALUE:
        return {"reply": f"Order (${total:,.2f}) exceeds the safety limit; not placed.",
                "pending": None}, 200
    try:
        res = shop_api.place_order(item_id, qty)
    except shop_api.ShopAPIError as e:
        return {"reply": e.message, "pending": None}, 200
    remaining = res.get("remaining_balance")
    msg = f"✅ Order placed: {qty} × {p['product_name']} for ${total:,.2f}."
    if remaining is not None:
        msg += f" Remaining balance: ${remaining:,.2f}."
    # Record the confirmation into the agent's context so it knows it's done.
    hist = session.get("agent_history", [])
    hist.append({"role": "user", "content": [{"text": f"[system] Order confirmed and placed: {msg}"}]})
    session["agent_history"] = hist[-20:]
    return {"reply": msg, "pending": None}


@app.route("/health")
def health():
    return {"status": "ok"}


# In-memory log of webhooks the shop calls back to us (Level 2: "receiving webhooks").
WEBHOOK_LOG: list[dict] = []


@app.route("/webhooks/incoming", methods=["POST", "GET"])
def webhooks_incoming():
    """Public endpoint the shop API calls back on events. Records what arrives."""
    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        WEBHOOK_LOG.append({"at": _now(), "payload": payload,
                            "headers": {k: v for k, v in request.headers.items()
                                        if k.lower() in ("content-type", "x-webhook-event")}})
        return {"received": True}, 200
    return {"count": len(WEBHOOK_LOG), "events": WEBHOOK_LOG[-10:]}


if __name__ == "__main__":
    with app.app_context():
        init_db()
    # Port is configurable (macOS AirPlay Receiver squats on 5000); default 8080.
    port = int(os.environ.get("PORT", "8080"))
    # threaded=True: handle concurrent requests (the Day-3 eval harness sends several
    # at once; the default single-threaded dev server wedges under concurrency).
    # debug=False: the reloader/debugger is not concurrency-friendly and leaks internals.
    app.run(host="0.0.0.0", port=port, threaded=True, debug=False)
