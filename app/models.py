from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import DateTime, Enum, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.domain.enums import BankPaymentStatus, IdempotencyOperation, OrderPaymentStatus, PaymentStatus, PaymentType


def utcnow() -> datetime:
    """Return the current time in UTC."""
    return datetime.now(timezone.utc)


class Order(Base):
    """Represents a customer order and the payments attached to it."""

    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    payment_status: Mapped[OrderPaymentStatus] = mapped_column(
        Enum(OrderPaymentStatus), default=OrderPaymentStatus.UNPAID, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    payments: Mapped[list["Payment"]] = relationship(back_populates="order", cascade="all, delete-orphan")

    def paid_amount(self) -> Decimal:
        """Calculate the amount actually paid after refunds."""
        total = Decimal("0.00")
        for payment in self.payments:
            total += payment.net_amount()
        return total

    def committed_amount(self) -> Decimal:
        """Calculate the amount reserved or paid by non-failed payments."""
        total = Decimal("0.00")
        for payment in self.payments:
            total += payment.committed_amount()
        return total

    def available_amount(self) -> Decimal:
        """Return how much of the order total is still available for payment."""
        return Decimal(self.total_amount) - self.committed_amount()


class Payment(Base):
    """Represents a single payment attempt for an order."""

    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), nullable=False, index=True)
    payment_type: Mapped[PaymentType] = mapped_column(Enum(PaymentType), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    refunded_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    status: Mapped[PaymentStatus] = mapped_column(Enum(PaymentStatus), default=PaymentStatus.PENDING, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    order: Mapped[Order] = relationship(back_populates="payments")
    bank_payment: Mapped["BankPayment | None"] = relationship(
        back_populates="payment", uselist=False, cascade="all, delete-orphan"
    )
    idempotency_keys: Mapped[list["IdempotencyKey"]] = relationship(back_populates="payment", cascade="all, delete-orphan")

    def refunded_total(self) -> Decimal:
        """Return the refunded amount normalized to a decimal value."""
        return Decimal(self.refunded_amount or Decimal("0.00"))

    def net_amount(self) -> Decimal:
        """Return the effective paid amount after refunds for settled payments."""
        if self.status in {None, PaymentStatus.PENDING, PaymentStatus.FAILED}:
            return Decimal("0.00")
        return Decimal(self.amount) - self.refunded_total()

    def committed_amount(self) -> Decimal:
        """Return the amount this payment reserves against the order total."""
        if self.status == PaymentStatus.FAILED:
            return Decimal("0.00")
        return Decimal(self.amount) - self.refunded_total()


class BankPayment(Base):
    """Stores external bank state for an acquiring payment."""

    __tablename__ = "bank_payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    payment_id: Mapped[int] = mapped_column(ForeignKey("payments.id"), unique=True, nullable=False)
    external_payment_id: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)
    status: Mapped[BankPaymentStatus] = mapped_column(
        Enum(BankPaymentStatus), default=BankPaymentStatus.NEW, nullable=False
    )
    last_error: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    payment: Mapped[Payment] = relationship(back_populates="bank_payment")


class IdempotencyKey(Base):
    """Stores idempotent request keys mapped to the payment returned to the client."""

    __tablename__ = "idempotency_keys"
    __table_args__ = (UniqueConstraint("operation", "key", name="uq_idempotency_operation_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    operation: Mapped[IdempotencyOperation] = mapped_column(Enum(IdempotencyOperation), nullable=False)
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(255), nullable=False)
    payment_id: Mapped[int] = mapped_column(ForeignKey("payments.id"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    payment: Mapped[Payment] = relationship(back_populates="idempotency_keys")
