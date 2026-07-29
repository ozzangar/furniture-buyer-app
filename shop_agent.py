"""Buyer agent — a shopping assistant that acts on the human's behalf.

Design principle: the LLM's output is UNTRUSTED input. It can only *propose*
tool calls; deterministic Python code here decides whether any side effect
happens. Security lives in this file, never in the prompt.

Gates (all hold even if the LLM is fully jailbroken):
- Fixed tool allowlist (4 handlers) — the LLM cannot invoke anything else.
- user_id is injected server-side — the LLM can never act as another user.
- place_order is STAGED, never executed here — a separate human-confirmed
  request (/agent/confirm in app.py) performs the real purchase.
- Hard spend cap + quantity clamp — deterministic, independent of the LLM.
- Loop iteration cap — the LLM cannot cause an unbounded tool loop.
"""
from __future__ import annotations

import os

import boto3

import shop_api

MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "anthropic.claude-3-5-sonnet-20241022-v2:0")
REGION = os.environ.get("AWS_REGION", "ap-southeast-2")

MAX_TOOL_ITERS = 5          # runaway-loop cap
MAX_QTY = 20                # per-order quantity clamp
MAX_ORDER_VALUE = 3000.0    # hard spend cap (circuit breaker), independent of balance
MAX_SEARCH_LIMIT = 24       # keep results (and context) bounded

SYSTEM_PROMPT_TEMPLATE = (
    "You are Boakea's furniture shopping assistant. You ONLY help users browse the "
    "furniture catalogue, check their balance, and buy furniture. Politely decline "
    "anything unrelated (no general chat, code, or other topics).\n"
    "The ONLY valid product categories are exactly these:\n{categories}\n"
    "When a user says something informal (e.g. 'tables', 'sofa', 'desk'), map it to the "
    "closest category above and pass that EXACT name to search_catalogue "
    "(e.g. 'tables' or 'desk' -> 'Tables & desks'; 'sofa' -> 'Sofas & armchairs'). "
    "If unsure which category fits, call search_catalogue with no category to list "
    "everything, or ask a brief clarifying question — do NOT guess a category name that "
    "isn't in the list above.\n"
    "Search matches the category exactly; it does NOT understand price, colour, or vibe, "
    "so do that reasoning yourself over the results. For open-ended or descriptive requests "
    "that don't map to one category ('cozy reading corner', 'something Scandinavian', 'a small "
    "table'), use semantic_search instead — then reason over the returned dimensions/prices to "
    "judge 'small', 'cheap', etc. (embeddings can't do numbers). Never invent products, prices, or "
    "IDs; only use what the tools return. Before buying you must call place_order, which "
    "asks the user to confirm — never claim an order is complete until a confirmation "
    "result says so."
)

_categories_cache: list[str] | None = None


def _system_prompt() -> str:
    global _categories_cache
    if _categories_cache is None:
        try:
            _categories_cache = shop_api.get_categories()
        except Exception:
            _categories_cache = []
    cats = "\n".join(f"- {c}" for c in _categories_cache) or "(unavailable)"
    return SYSTEM_PROMPT_TEMPLATE.format(categories=cats)

# --- tool schemas the model sees (Bedrock Converse format) -------------------
TOOLS = [
    {"toolSpec": {
        "name": "search_catalogue",
        "description": "Search furniture products by exact category name (case-insensitive). "
                       "Returns items with item_id, product_name, price, category. No images.",
        "inputSchema": {"json": {"type": "object", "properties": {
            "category": {"type": "string", "description": "Exact category, e.g. 'Chairs'"},
            "limit": {"type": "integer", "description": "Max results (<=24)"}}}}}},
    {"toolSpec": {
        "name": "product_detail",
        "description": "Get full detail (price, dimensions, colours) for one product by item_id.",
        "inputSchema": {"json": {"type": "object", "properties": {
            "item_id": {"type": "string"}}, "required": ["item_id"]}}}},
    {"toolSpec": {
        "name": "check_balance",
        "description": "Get the current user's remaining balance in dollars.",
        "inputSchema": {"json": {"type": "object", "properties": {}}}}},
    {"toolSpec": {
        "name": "semantic_search",
        "description": "Search products by MEANING / vibe, for open-ended or descriptive requests "
                       "that don't map to an exact category — e.g. 'cozy reading corner', 'something "
                       "Scandinavian', 'a small table for a tight space'. Returns products with price "
                       "and dimensions; reason over those yourself for size/budget. Prefer "
                       "search_catalogue when the user names a clear category.",
        "inputSchema": {"json": {"type": "object", "properties": {
            "query": {"type": "string", "description": "The user's descriptive request, in natural language"}},
            "required": ["query"]}}}},
    {"toolSpec": {
        "name": "place_order",
        "description": "Propose buying an item for the current user. This does NOT complete the "
                       "purchase — it asks the user to confirm first. Use when the user wants to buy.",
        "inputSchema": {"json": {"type": "object", "properties": {
            "item_id": {"type": "string"},
            "quantity": {"type": "integer", "description": "How many (default 1)"}},
            "required": ["item_id"]}}}},
]

_client = None


def _bedrock():
    global _client
    if _client is None:
        _client = boto3.client("bedrock-runtime", region_name=REGION)
    return _client


# --- deterministic tool handlers (LLM args are untrusted) --------------------
def _resolve_category(name: str) -> str | None:
    """Map a possibly-informal category to a real one: exact → substring → word overlap.
    Safety net so 'tables'/'sofa'/'desk' still work if the model's guess isn't exact."""
    cats = _categories_cache if _categories_cache is not None else shop_api.get_categories()
    n = name.strip().lower()
    if not n:
        return None
    for c in cats:                                   # exact
        if c.lower() == n:
            return c
    for c in cats:                                   # user term inside a category or vice-versa
        cl = c.lower()
        if n in cl or cl in n:
            return c
    nwords = set(n.replace("&", " ").split())        # any shared significant word
    for c in cats:
        cwords = set(c.lower().replace("&", " ").split())
        if nwords & cwords:
            return c
    return None


def _h_search(args: dict) -> dict:
    raw_cat = str(args.get("category") or "").strip()
    category = _resolve_category(raw_cat) if raw_cat else None
    limit = args.get("limit") or MAX_SEARCH_LIMIT
    try:
        limit = max(1, min(int(limit), MAX_SEARCH_LIMIT))
    except (TypeError, ValueError):
        limit = MAX_SEARCH_LIMIT
    # If the requested category couldn't be resolved, tell the model (so it can guide the user).
    if raw_cat and category is None:
        return {"count": 0, "products": [],
                "note": f"'{raw_cat}' is not a valid category. Valid categories: "
                        + ", ".join(_categories_cache or [])}
    products = shop_api.list_products(category=category, limit=limit)
    slim = [{"item_id": p["item_id"], "product_name": p["product_name"],
             "price": p["price"], "category": p["category"]} for p in products]
    return {"count": len(slim), "products": slim}


def _h_detail(args: dict) -> dict:
    item_id = str(args.get("item_id") or "").strip()
    p = shop_api.get_product(item_id)
    if p is None:
        return {"error": "no such item_id"}
    return {"item_id": p["item_id"], "product_name": p["product_name"], "price": p["price"],
            "category": p["category"], "colours": p.get("colours"),
            "width": p.get("width"), "height": p.get("height"), "depth": p.get("depth")}


def _h_balance(_args: dict) -> dict:
    return {"balance": shop_api.get_balance().get("balance")}


def _h_semantic_search(args: dict) -> dict:
    """Vector-RAG search by meaning. Returns products (with price + dimensions) so the
    model can then reason over size/budget itself."""
    import rag
    query = str(args.get("query") or "").strip()
    if not query:
        return {"count": 0, "products": []}
    try:
        results = rag.search(query, k=6)
    except Exception as e:
        return {"error": f"semantic search unavailable: {type(e).__name__}"}
    return {"count": len(results), "products": results}


def _h_place_order(args: dict) -> dict:
    """STAGE an order for human confirmation — never actually buys here.

    Returns a 'pending' descriptor with the REAL price re-fetched from the API
    (not whatever the model might claim). app.py turns this into a confirm prompt.
    """
    item_id = str(args.get("item_id") or "").strip()
    try:
        qty = max(1, min(int(args.get("quantity") or 1), MAX_QTY))
    except (TypeError, ValueError):
        qty = 1
    p = shop_api.get_product(item_id)
    if p is None:
        return {"error": "no such item_id — cannot order"}
    total = round(p["price"] * qty, 2)
    if total > MAX_ORDER_VALUE:
        return {"refused": True,
                "reason": f"order total ${total:,.2f} exceeds the ${MAX_ORDER_VALUE:,.0f} "
                          f"per-order safety limit"}
    return {"pending_confirmation": True, "item_id": item_id,
            "product_name": p["product_name"], "quantity": qty,
            "unit_price": p["price"], "total_price": total}


HANDLERS = {
    "search_catalogue": _h_search,
    "semantic_search": _h_semantic_search,
    "product_detail": _h_detail,
    "check_balance": _h_balance,
    "place_order": _h_place_order,
}


def run_turn(history: list[dict], user_text: str) -> dict:
    """Run one user turn through the tool-calling loop.

    `history` is the prior Bedrock-format messages list (mutated + returned).
    Returns {"reply": str, "history": [...], "pending": dict|None}.
    `pending` (if set) is a staged order awaiting /agent/confirm.
    """
    user_text = (user_text or "").strip()[:1000]  # clamp input length
    if not user_text:
        return {"reply": "Please type a request.", "history": history, "pending": None,
                "products": []}

    messages = list(history) + [{"role": "user", "content": [{"text": user_text}]}]
    pending = None
    referenced: dict[str, dict] = {}   # products the agent looked at this turn (by item_id)

    def _remember(items: list[dict]):
        for p in items:
            iid = p.get("item_id")
            if iid and iid not in referenced:
                referenced[iid] = {"item_id": iid, "product_name": p.get("product_name"),
                                   "price": p.get("price"),
                                   "image_url": shop_api.product_image_url(iid)}

    for _ in range(MAX_TOOL_ITERS):
        resp = _bedrock().converse(
            modelId=MODEL_ID, messages=messages, system=[{"text": _system_prompt()}],
            toolConfig={"tools": TOOLS}, inferenceConfig={"maxTokens": 1024},
        )
        out = resp["output"]["message"]
        messages.append(out)

        if resp.get("stopReason") != "tool_use":
            reply = "".join(b.get("text", "") for b in out["content"]).strip()
            return {"reply": reply or "(no response)", "history": messages, "pending": pending,
                    "products": list(referenced.values())[:6]}

        # Execute each requested tool through the allowlist; feed results back.
        tool_results = []
        for block in out["content"]:
            if "toolUse" not in block:
                continue
            tu = block["toolUse"]
            handler = HANDLERS.get(tu["name"])
            result = handler(tu.get("input") or {}) if handler else {"error": "unknown tool"}
            if isinstance(result, dict):
                if result.get("pending_confirmation"):
                    pending = result  # staged order; app.py gates the real buy
                if result.get("products"):                 # from search_catalogue
                    _remember(result["products"])
                elif result.get("item_id"):                # from product_detail
                    _remember([result])
            tool_results.append({"toolResult": {
                "toolUseId": tu["toolUseId"],
                "content": [{"json": result}]}})
        messages.append({"role": "user", "content": tool_results})

    # Hit the loop cap — return whatever text we have rather than looping forever.
    return {"reply": "I've done as much as I safely can in one go — could you narrow that down?",
            "history": messages, "pending": pending, "products": list(referenced.values())[:6]}
