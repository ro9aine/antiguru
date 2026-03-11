from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import DateTime, Enum, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.domain.enums import BankPaymentStatus, OrderPaymentStatus, PaymentStatus, PaymentType
from app.domain.exceptions import PaymentValidationError


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    payment_status: Mapped[OrderPaymentStatus] = mapped_column(
        Enum(OrderPaymentStatus), default=OrderPaymentStatus.UNPAID, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    payments: Mapped[list["Payment"]] = relationship(back_populates="order", cascade="all, delete-orphan")

    def paid_amount(self) -> Decimal:
        total = Decimal("0.00")
        for payment in self.payments:
            total += payment.net_amount()
        return total

    def committed_amount(self) -> Decimal:
        total = Decimal("0.00")
        for payment in self.payments:
            total += payment.committed_amount()
        return total

    def available_amount(self) -> Decimal:
        return Decimal(self.total_amount) - self.committed_amount()

    def recalculate_payment_status(self) -> None:
        paid = self.paid_amount()
        total = Decimal(self.total_amount)
        if paid <= Decimal("0.00"):
            self.payment_status = OrderPaymentStatus.UNPAID
        elif paid < total:
            self.payment_status = OrderPaymentStatus.PARTIALLY_PAID
        else:
            self.payment_status = OrderPaymentStatus.PAID


class Payment(Base):
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

    def net_amount(self) -> Decimal:
        if self.status in {PaymentStatus.PENDING, PaymentStatus.FAILED}:
            return Decimal("0.00")
        return Decimal(self.amount) - Decimal(self.refunded_amount)

    def committed_amount(self) -> Decimal:
        if self.status == PaymentStatus.FAILED:
            return Decimal("0.00")
        return Decimal(self.amount) - Decimal(self.refunded_amount)

    def deposit(self, order: Order, paid_at: datetime | None = None) -> None:
        if self.status in {PaymentStatus.SUCCEEDED, PaymentStatus.PARTIALLY_REFUNDED, PaymentStatus.REFUNDED}:
            raise PaymentValidationError("payment is already deposited")
        if Decimal(self.amount) <= Decimal("0.00"):
            raise PaymentValidationError("payment amount must be positive")
        if Decimal(self.amount) > order.available_amount():
            raise PaymentValidationError("payment amount exceeds order remaining amount")

        self.status = PaymentStatus.SUCCEEDED
        self.paid_at = paid_at or utcnow()
        order.recalculate_payment_status()

    def refund(self, order: Order, amount: Decimal) -> None:
        if self.status not in {
            PaymentStatus.SUCCEEDED,
            PaymentStatus.PARTIALLY_REFUNDED,
        }:
            raise PaymentValidationError("only successful payments can be refunded")
        if amount <= Decimal("0.00"):
            raise PaymentValidationError("refund amount must be positive")

        available_refund = Decimal(self.amount) - Decimal(self.refunded_amount)
        if amount > available_refund:
            raise PaymentValidationError("refund amount exceeds refundable amount")

        self.refunded_amount = Decimal(self.refunded_amount) + amount
        self.status = (
            PaymentStatus.REFUNDED
            if Decimal(self.refunded_amount) == Decimal(self.amount)
            else PaymentStatus.PARTIALLY_REFUNDED
        )
        order.recalculate_payment_status()


class BankPayment(Base):
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
