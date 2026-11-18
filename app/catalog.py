from decimal import Decimal

CATALOG = {
    "desk_lamp": {
        "id": "desk_lamp",
        "name": "Adjustable LED Desk Lamp",
        "description": "Sample catalogue item for the NEXTPAY demo.",
        "price": Decimal("24.99"),
        "currency": "USD",
        "category": "workspace",
    },
    "keyboard": {
        "id": "keyboard",
        "name": "Compact Wireless Keyboard",
        "description": "Sample catalogue item for the NEXTPAY demo.",
        "price": Decimal("39.50"),
        "currency": "USD",
        "category": "workspace",
    },
    "headphones": {
        "id": "headphones",
        "name": "Over-Ear Headphones",
        "description": "Sample catalogue item for the NEXTPAY demo.",
        "price": Decimal("59.00"),
        "currency": "USD",
        "category": "audio",
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