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
@app.route("/")
def index():
    category = request.args.get("category") or None
    try:
        products = shop_api.list_products(category=category, limit=60)
        categories = shop_api.get_categories()
    except shop_api.ShopAPIError as e:
        flash(f"Catalogue unavailable: {e.message}", "error")
        products, categories = [], []
    return render_template(
        "index.html", products=products, categories=categories, active_category=category
    )


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


@app.route("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    with app.app_context():
        init_db()
    # Port is configurable (macOS AirPlay Receiver squats on 5000); default 8080.
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="127.0.0.1", port=port, debug=True)
