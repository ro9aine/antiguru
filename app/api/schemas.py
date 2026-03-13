from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.domain.enums import BankPaymentStatus, OrderPaymentStatus, PaymentStatus, PaymentType
from app.models import Order, Payment


class PaymentCreateRequest(BaseModel):
    amount: Decimal = Field(gt=Decimal("0.00"))
    payment_type: PaymentType


class RefundRequest(BaseModel):
    amount: Decimal = Field(gt=Decimal("0.00"))


class BankWebhookRequest(BaseModel):
    payment_id: str
    amount: Decimal = Field(ge=Decimal("0.00"))
    status: BankPaymentStatus
    paid_at: datetime | None = None


class BankPaymentResponse(BaseModel):
    external_payment_id: str | None
    status: BankPaymentStatus
    last_error: str | None
    last_synced_at: datetime | None
    paid_at: datetime | None


class PaymentResponse(BaseModel):
    id: int
    order_id: int
    payment_type: PaymentType
    amount: Decimal
    refunded_amount: Decimal
    status: PaymentStatus
    paid_at: datetime | None
    bank_payment: BankPaymentResponse | None

    @classmethod
    def from_model(cls, payment: Payment) -> "PaymentResponse":
        return cls(
            id=payment.id,
            order_id=payment.order_id,
            payment_type=payment.payment_type,
            amount=payment.amount,
            refunded_amount=payment.refunded_amount,
            status=payment.status,
            paid_at=payment.paid_at,
            bank_payment=(
                BankPaymentResponse(
                    external_payment_id=payment.bank_payment.external_payment_id,
                    status=payment.bank_payment.status,
                    last_error=payment.bank_payment.last_error,
                    last_synced_at=payment.bank_payment.last_synced_at,
                    paid_at=payment.bank_payment.paid_at,
                )
                if payment.bank_payment
                else None
            ),
        )


class OrderResponse(BaseModel):
    id: int
    total_amount: Decimal
    paid_amount: Decimal
    payment_status: OrderPaymentStatus

    @classmethod
    def from_model(cls, order: Order) -> "OrderResponse":
        return cls(
            id=order.id,
            total_amount=order.total_amount,
            paid_amount=order.paid_amount(),
            payment_status=order.payment_status,
        )
