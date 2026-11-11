from decimal import Decimal

CATALOG = {
    "desk_lamp": {
        "id": "desk_lamp",
        "name": "Adjustable LED Desk Lamp",
        "description": "LED desk lamp suitable for studying and reading.",
        "price": Decimal("24.99"),
        "currency": "USD",
        "category": "workspace",
        "keywords": [
            "desk lamp", "lamp", "study lamp",
            "reading lamp", "lighting"
        ],
    },
    "keyboard": {
        "id": "keyboard",
        "name": "Compact Wireless Keyboard",
        "description": "Compact wireless keyboard for computers and laptops.",
        "price": Decimal("39.50"),
        "currency": "USD",
        "category": "workspace",
        "keywords": [
            "keyboard", "wireless keyboard",
            "computer keyboard", "typing"
        ],
    },
    "headphones": {
        "id": "headphones",
        "name": "Over-Ear Headphones",
        "description": "Over-ear headphones for music and studying.",
        "price": Decimal("59.00"),
        "currency": "USD",
        "category": "audio",
        "keywords": [
            "headphones", "earphones", "headset",
            "audio", "music"
        ],
    },
}


def list_products() -> list[dict]:
    """Return JSON-friendly product data from the trusted server catalogue."""
    return [
        {
            **product,
            "price": str(product["price"]),
            "sample_data": True,
        }
        for product in CATALOG.values()
    ]


def get_product(product_id: str) -> dict | None:
    return CATALOG.get(product_id)