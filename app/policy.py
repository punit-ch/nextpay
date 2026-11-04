from decimal import Decimal, ROUND_HALF_UP

from app.catalog import get_product

MAX_BUDGET = Decimal("1000.00")
MAX_QUANTITY = 5
CURRENCY = "USD"


class PolicyViolation(ValueError):
    pass


def calculate_total(product_id: str, quantity: int) -> Decimal:
    if not isinstance(quantity, int) or isinstance(quantity, bool):
        raise PolicyViolation("Quantity must be an integer.")

    if quantity < 1 or quantity > MAX_QUANTITY:
        raise PolicyViolation(
            f"Quantity must be between 1 and {MAX_QUANTITY}."
        )

    product = get_product(product_id)
    if product is None:
        raise PolicyViolation("Unknown product.")

    total = product["price"] * quantity
    return total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def validate_purchase(
    product_id: str,
    quantity: int,
    budget_limit: Decimal,
) -> dict:
    if budget_limit <= Decimal("0"):
        raise PolicyViolation("Budget must be greater than zero.")

    if budget_limit > MAX_BUDGET:
        raise PolicyViolation(
            f"Demo budget cannot exceed {CURRENCY} {MAX_BUDGET}."
        )

    product = get_product(product_id)
    if product is None:
        raise PolicyViolation("Unknown product.")

    total = calculate_total(product_id, quantity)

    if total > budget_limit:
        raise PolicyViolation(
            f"Total {total} {CURRENCY} exceeds budget {budget_limit}."
        )

    return {
        "product_id": product_id,
        "product_name": product["name"],
        "quantity": quantity,
        "unit_price": str(product["price"]),
        "total": str(total),
        "currency": CURRENCY,
        "budget_limit": str(budget_limit),
        "within_budget": True,
    }