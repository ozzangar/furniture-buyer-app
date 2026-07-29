"""Client for the furniture-shop API (the external service, Step 5).

One place that knows how to talk to the shop: base URL, key, request shapes,
retry-on-429, and error mapping. The rest of the app calls these functions and
never touches HTTP directly. Verified against the live OpenAPI spec 2026-07-29.
"""
from __future__ import annotations

import os
import time

import requests


class ShopAPIError(Exception):
    """A shop API call failed in a way the UI should show the user."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.message = message
        self.status = status


def _base() -> str:
    return os.environ.get("SHOP_API_BASE_URL", "https://day1.training.cognitivo.com.au").rstrip("/")


def _user_id() -> str:
    return os.environ.get("SHOP_USER_ID", "")


def _headers(auth: bool) -> dict:
    h = {"Accept": "application/json"}
    if auth:
        h["X-Api-Key"] = os.environ.get("SHOP_API_KEY", "")
    return h


def _request(method: str, path: str, *, auth: bool = False, json_body: dict | None = None,
             params: dict | None = None, timeout: int = 20, _retries: int = 2) -> requests.Response:
    """HTTP with polite 429 back-off (honours Retry-After) and one-place error handling."""
    url = f"{_base()}{path}"
    for attempt in range(_retries + 1):
        try:
            resp = requests.request(method, url, headers=_headers(auth), json=json_body,
                                     params=params, timeout=timeout)
        except requests.RequestException as e:
            raise ShopAPIError(f"Could not reach the shop service: {e}") from e
        if resp.status_code == 429 and attempt < _retries:
            wait = float(resp.headers.get("Retry-After", "2"))
            time.sleep(min(wait, 10))
            continue
        return resp
    return resp  # type: ignore[return-value]


# --- catalogue (public, no auth) --------------------------------------------
def list_products(category: str | None = None, limit: int = 60, skip: int = 0) -> list[dict]:
    """Products via the lightweight search-index (no images). limit capped at 1000 by the API."""
    params: dict = {"limit": max(1, min(limit, 1000)), "skip": max(0, skip)}
    if category:
        params["category"] = category
    resp = _request("GET", "/catalogue/search-index", params=params)
    if resp.status_code != 200:
        raise ShopAPIError("Could not load the catalogue.", resp.status_code)
    return resp.json()


def get_categories() -> list[str]:
    resp = _request("GET", "/catalogue/categories")
    return resp.json() if resp.status_code == 200 else []


def get_product(item_id: str) -> dict | None:
    """One product's full detail (includes base64 image in image_url). None if unknown (404)."""
    resp = _request("GET", f"/catalogue/{item_id}")
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        raise ShopAPIError("Could not load that product.", resp.status_code)
    return resp.json()


def product_image_url(item_id: str) -> str:
    """URL of the dedicated raw-bytes image endpoint (point an <img src> at it)."""
    return f"{_base()}/catalogue/{item_id}/image"


# --- account (needs key) -----------------------------------------------------
def get_balance() -> dict:
    """{'user_id', 'name', 'balance'} for the configured user."""
    resp = _request("GET", f"/users/{_user_id()}", auth=True)
    if resp.status_code == 401:
        raise ShopAPIError("Shop API key missing or invalid.", 401)
    if resp.status_code != 200:
        raise ShopAPIError("Could not read your balance.", resp.status_code)
    return resp.json()


def place_order(item_id: str, quantity: int = 1) -> dict:
    """Place a real order (debits real balance). Maps known failures to clear messages.

    Returns the OrderResult dict: {order_id, status, items[], total_price, remaining_balance?}.
    """
    body = {"user_id": _user_id(), "items": [{"item_id": item_id, "quantity": max(1, quantity)}]}
    resp = _request("POST", "/orders", auth=True, json_body=body)
    if resp.status_code == 200:
        return resp.json()
    if resp.status_code == 402:
        raise ShopAPIError("Insufficient balance for this order.", 402)
    if resp.status_code == 404:
        raise ShopAPIError("That item is no longer available.", 404)
    if resp.status_code == 401:
        raise ShopAPIError("Shop API key missing or invalid.", 401)
    if resp.status_code == 403:
        raise ShopAPIError("Not allowed to order for this user.", 403)
    # surface the API's own detail if present
    try:
        detail = resp.json().get("detail")
    except Exception:
        detail = None
    raise ShopAPIError(f"Order failed ({resp.status_code}).{f' {detail}' if detail else ''}",
                       resp.status_code)


def get_order_history() -> list[dict]:
    resp = _request("GET", f"/orders/{_user_id()}", auth=True)
    return resp.json() if resp.status_code == 200 else []


def register_webhook(callback_url: str, events: list[str] | None = None) -> dict:
    """Register a callback URL so the shop notifies us of order events."""
    body = {"user_id": _user_id(), "url": callback_url}
    if events:
        body["events"] = events
    resp = _request("POST", "/webhooks", auth=True, json_body=body)
    if resp.status_code not in (200, 201):
        raise ShopAPIError(f"Could not register webhook ({resp.status_code}).", resp.status_code)
    return resp.json()


def list_webhooks() -> list[dict]:
    resp = _request("GET", f"/webhooks/{_user_id()}", auth=True)
    return resp.json() if resp.status_code == 200 else []


def delete_webhook(webhook_id: str) -> bool:
    resp = _request("DELETE", f"/webhooks/{webhook_id}", auth=True)
    return resp.status_code in (200, 204)


def get_invoice_pdf(order_id: str) -> bytes:
    """Raw PDF bytes for an order's invoice. Needs the key (fetched server-side).

    Raises ShopAPIError on failure (e.g. 404 unknown order, 401 bad key).
    """
    resp = _request("GET", f"/orders/{order_id}/invoice", auth=True, timeout=30)
    if resp.status_code == 404:
        raise ShopAPIError("Invoice not found for that order.", 404)
    if resp.status_code != 200:
        raise ShopAPIError("Could not fetch that invoice.", resp.status_code)
    return resp.content
