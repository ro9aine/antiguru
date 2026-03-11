from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from app.domain.enums import BankPaymentStatus, PaymentStatus, PaymentType
from app.domain.exceptions import OrderNotFoundError, PaymentNotFoundError, PaymentValidationError
from app.integrations.bank_client import BankApiClient
from app.models import BankPayment, Payment, utcnow
from app.repositories import OrderRepository, PaymentRepository


class PaymentService:
    def __init__(self, session: Session, bank_client: BankApiClient | None = None) -> None:
        self.session = session
        self.orders = OrderRepository(session)
        self.payments = PaymentRepository(session)
        self.bank_client = bank_client or BankApiClient()

    def create_payment(self, order_id: int, amount: Decimal, payment_type: PaymentType) -> Payment:
        order = self.orders.get(order_id)
        if order is None:
            raise OrderNotFoundError(f"order {order_id} not found")
        if amount <= Decimal("0.00"):
            raise PaymentValidationError("payment amount must be positive")
        if amount > order.available_amount():
            raise PaymentValidationError("payment amount exceeds order remaining amount")

        payment = Payment(order=order, amount=amount, payment_type=payment_type)
        self.payments.add(payment)

        if payment_type == PaymentType.CASH:
            payment.deposit(order)
        else:
            bank_payment = BankPayment(payment=payment, status=BankPaymentStatus.PENDING)
            payment.bank_payment = bank_payment
            try:
                start_result = self.bank_client.start_payment(order.id, amount)
                bank_payment.external_payment_id = start_result.external_payment_id
                bank_payment.last_error = None
            except Exception as exc:
                bank_payment.status = BankPaymentStatus.FAILED
                bank_payment.last_error = str(exc)
                payment.status = PaymentStatus.FAILED
                self.session.commit()
                raise

        order.recalculate_payment_status()
        self.session.commit()
        self.session.refresh(payment)
        return payment

    def refund_payment(self, payment_id: int, amount: Decimal) -> Payment:
        payment = self.payments.get(payment_id)
        if payment is None:
            raise PaymentNotFoundError(f"payment {payment_id} not found")

        payment.refund(payment.order, amount)
        self.session.commit()
        self.session.refresh(payment)
        return payment

    def sync_bank_payment(self, payment_id: int) -> Payment:
        payment = self.payments.get(payment_id)
        if payment is None:
            raise PaymentNotFoundError(f"payment {payment_id} not found")
        if payment.payment_type != PaymentType.ACQUIRING or payment.bank_payment is None:
            raise PaymentValidationError("payment is not an acquiring payment")
        if not payment.bank_payment.external_payment_id:
            raise PaymentValidationError("bank payment does not have external id")

        result = self.bank_client.check_payment(payment.bank_payment.external_payment_id)
        payment.bank_payment.status = result.status
        payment.bank_payment.last_synced_at = utcnow()
        payment.bank_payment.paid_at = result.paid_at
        payment.bank_payment.last_error = None if result.status != BankPaymentStatus.NOT_FOUND else "payment not found"

        if result.status == BankPaymentStatus.PAID and payment.status == PaymentStatus.PENDING:
            if result.amount != payment.amount:
                raise PaymentValidationError("bank payment amount does not match internal payment amount")
            payment.deposit(payment.order, paid_at=result.paid_at)
        elif result.status in {BankPaymentStatus.FAILED, BankPaymentStatus.NOT_FOUND}:
            payment.status = PaymentStatus.FAILED

        payment.order.recalculate_payment_status()
        self.session.commit()
        self.session.refresh(payment)
        return payment
