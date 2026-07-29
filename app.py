"""Furniture Buyer App — Level 1.

A small Flask app: browse a catalogue, log in, place orders against a budget,
and see a report of your own orders. Catalogue data is placeholder for now
(Step 5 swaps in the real shop API). Own data (users, orders) lives in SQLite.
"""
from __future__ import annotations

import os
import sqlite3
import uuid
from datetime import datetime, timezone

from flask import (
    Flask, g, redirect, render_template, request, session, url_for, flash, abort,
)
from werkzeug.security import check_password_hash, generate_password_hash

from catalogue import get_products, get_product

# --- config -----------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data", "app.db")
STARTING_BALANCE = 2500.0  # per-user starting budget (Level 1 local value)

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
            balance       REAL NOT NULL,
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


@app.context_processor
def inject_user() -> dict:
    return {"user": current_user()}


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
            "INSERT INTO customer (username, password_hash, display_name, balance, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (username, generate_password_hash(password, method=PW_METHOD), display,
             STARTING_BALANCE, _now()),
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


# --- catalogue + ordering ----------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html", products=get_products())


@app.route("/product/<item_id>")
def product(item_id: str):
    p = get_product(item_id)
    if p is None:
        abort(404)
    return render_template("product.html", product=p)


@app.route("/buy/<item_id>", methods=["POST"])
def buy(item_id: str):
    user = current_user()
    if user is None:
        flash("Please log in to place an order.", "error")
        return redirect(url_for("login"))
    p = get_product(item_id)
    if p is None:
        abort(404)
    try:
        qty = max(1, int(request.form.get("quantity", "1")))
    except ValueError:
        qty = 1
    total = round(p["price"] * qty, 2)

    # Controller rule: cannot spend more than the remaining balance.
    if total > user["balance"]:
        flash(
            f"Insufficient balance: this costs ${total:,.2f} but you have "
            f"${user['balance']:,.2f}.",
            "error",
        )
        return redirect(url_for("product", item_id=item_id))

    db = get_db()
    order_id = uuid.uuid4().hex[:12]
    db.execute(
        'INSERT INTO "order" (id, customer_id, placed_at, total_price, status)'
        " VALUES (?, ?, ?, ?, ?)",
        (order_id, user["id"], _now(), total, "confirmed"),
    )
    db.execute(
        "INSERT INTO order_line (order_id, item_id, product_name, quantity, unit_price)"
        " VALUES (?, ?, ?, ?, ?)",
        (order_id, p["item_id"], p["product_name"], qty, p["price"]),
    )
    db.execute(
        "UPDATE customer SET balance = balance - ? WHERE id = ?", (total, user["id"])
    )
    db.commit()
    flash(f"Order placed: {qty} × {p['product_name']} for ${total:,.2f}.", "ok")
    return redirect(url_for("orders"))


@app.route("/orders")
def orders():
    user = current_user()
    if user is None:
        flash("Please log in to see your orders.", "error")
        return redirect(url_for("login"))
    db = get_db()
    rows = db.execute(
        'SELECT * FROM "order" WHERE customer_id = ? ORDER BY placed_at DESC',
        (user["id"],),
    ).fetchall()
    orders_with_lines = []
    for o in rows:
        lines = db.execute(
            "SELECT * FROM order_line WHERE order_id = ?", (o["id"],)
        ).fetchall()
        orders_with_lines.append({"order": o, "lines": lines})
    total_spent = sum(o["total_price"] for o in rows)
    return render_template(
        "orders.html", orders=orders_with_lines, total_spent=total_spent
    )


@app.route("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    with app.app_context():
        init_db()
    # Port is configurable (macOS AirPlay Receiver squats on 5000); default 8080.
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="127.0.0.1", port=port, debug=True)
