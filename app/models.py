
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    String,
    Text,
    ForeignKey,
)
from sqlalchemy.orm import relationship

from app.database import Base


class PurchasePlan(Base):
    __tablename__ = "purchase_plans"

    plan_id = Column(String, primary_key=True, index=True)
    goal = Column(Text, nullable=False)
    product_id = Column(String, nullable=False)
    product_name = Column(String, nullable=False)
    quantity = Column(Integer, nullable=False)
    unit_price = Column(String, nullable=False)
    total = Column(String, nullable=False)
    budget_limit = Column(String, nullable=False)

    status = Column(String, nullable=False)
    payment_status = Column(
        String,
        nullable=False,
        default="not_started",
    )

    paypal_order_id = Column(String, unique=True, nullable=True)
    paypal_capture_id = Column(String, unique=True, nullable=True)

    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

class PlanAccess(Base):
    """Hashed capability credential granting access to one purchase plan."""

    __tablename__ = "plan_access"

    plan_id = Column(
        String,
        ForeignKey("purchase_plans.plan_id", ondelete="CASCADE"),
        primary_key=True,
    )
    token_hash = Column(String(64), nullable=False, unique=True)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class Payment(Base):
    __tablename__ = "payments"

    payment_id = Column(String, primary_key=True, index=True)

    plan_id = Column(
        String,
        ForeignKey("purchase_plans.plan_id"),
        nullable=False,
        index=True,
    )

    paypal_order_id = Column(String, unique=True, nullable=False)
    paypal_capture_id = Column(String, unique=True, nullable=True)

    amount = Column(String, nullable=False)
    currency = Column(String, nullable=False, default="USD")

    status = Column(String, nullable=False, default="created")

    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class PaymentEvent(Base):
    """Append-only audit trail of payment state changes and reconciliations.

    Rows hold status codes and short reason codes only: never PayPal payloads,
    payer details, tokens or credentials.
    """

    __tablename__ = "payment_events"

    event_id = Column(Integer, primary_key=True, autoincrement=True)
    plan_id = Column(
        String,
        ForeignKey("purchase_plans.plan_id"),
        nullable=False,
        index=True,
    )
    paypal_order_id = Column(String, nullable=True, index=True)
    event_type = Column(String, nullable=False)
    from_status = Column(String, nullable=True)
    to_status = Column(String, nullable=True)
    detail = Column(String, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
