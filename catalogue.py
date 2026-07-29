"""Catalogue data source.

Level 1 uses placeholder products so the app runs before the real API is wired
in. In Step 5 this module is swapped to fetch from the shop's catalogue API
(GET /catalogue/search-index etc.) — the rest of the app calls get_products()
/ get_product() and doesn't care where the data comes from.
"""
from __future__ import annotations

# Placeholder catalogue — shaped like the real API's product records
# (item_id, product_name, price, category, colours) so the swap in Step 5 is clean.
_PRODUCTS: list[dict] = [
    {"item_id": "CHR-001", "product_name": "Aria Accent Chair", "price": 399.0,
     "category": "Chairs", "colours": ["mustard"]},
    {"item_id": "CHR-002", "product_name": "Nord Dining Chair", "price": 149.0,
     "category": "Chairs", "colours": ["oak", "black"]},
    {"item_id": "TBL-001", "product_name": "Linnea Dining Table", "price": 899.0,
     "category": "Tables", "colours": ["walnut"]},
    {"item_id": "TBL-002", "product_name": "Halden Side Table", "price": 129.0,
     "category": "Tables", "colours": ["white", "oak"]},
    {"item_id": "SFA-001", "product_name": "Copenhagen 3-Seat Sofa", "price": 1499.0,
     "category": "Sofas", "colours": ["forest green"]},
    {"item_id": "SFA-002", "product_name": "Malmo Loveseat", "price": 949.0,
     "category": "Sofas", "colours": ["charcoal"]},
    {"item_id": "BED-001", "product_name": "Fjord Bed Frame (Queen)", "price": 799.0,
     "category": "Beds", "colours": ["oak"]},
    {"item_id": "LMP-001", "product_name": "Arc Floor Lamp", "price": 199.0,
     "category": "Lighting", "colours": ["brass"]},
]

_BY_ID = {p["item_id"]: p for p in _PRODUCTS}


def get_products(category: str | None = None) -> list[dict]:
    """All products, optionally filtered by exact (case-insensitive) category."""
    if category:
        cat = category.strip().lower()
        return [p for p in _PRODUCTS if p["category"].lower() == cat]
    return list(_PRODUCTS)


def get_product(item_id: str) -> dict | None:
    """One product by id, or None if unknown."""
    return _BY_ID.get(item_id)


def get_categories() -> list[str]:
    return sorted({p["category"] for p in _PRODUCTS})
