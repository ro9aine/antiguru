from __future__ import annotations

from decimal import Decimal

from app.domain.enums import BankPaymentStatus, OrderPaymentStatus, PaymentStatus, PaymentType
from app.domain.exceptions import OrderNotFoundError, PaymentNotFoundError, PaymentValidationError
from app.integrations.bank_client import BankApiClient
from app.models import BankPayment, Order, Payment, utcnow
from app.repositories import OrderRepository, PaymentRepository


class PaymentService:
    """Coordinates payment workflows using repository and bank-client abstractions."""

    def __init__(
        self,
        orders: OrderRepository,
        payments: PaymentRepository,
        bank_client: BankApiClient | None = None,
    ) -> None:
        """Build a service instance with repositories for orders and payments."""
        self.orders = orders
        self.payments = payments
        self.bank_client = bank_client or BankApiClient()

    def create_payment(self, order_id: int, amount: Decimal, payment_type: PaymentType) -> Payment:
        """Create a payment, optionally opening an acquiring payment with the bank."""
        order = self.orders.get(order_id)
        if order is None:
            raise OrderNotFoundError(f"order {order_id} not found")
        if amount <= Decimal("0.00"):
            raise PaymentValidationError("payment amount must be positive")
        if amount > order.available_amount():
            raise PaymentValidationError("payment amount exceeds order remaining amount")

        payment = Payment(
            order=order,
            amount=amount,
            payment_type=payment_type,
            status=PaymentStatus.PENDING,
            refunded_amount=Decimal("0.00"),
        )
        self.payments.add(payment)

        if payment_type == PaymentType.CASH:
            self._deposit_payment(payment, order)
        else:
            bank_payment = BankPayment(payment=payment, status=BankPaymentStatus.PENDING)
            payment.bank_payment = bank_payment
            self.payments.add_bank_payment(bank_payment)
            try:
                start_result = self.bank_client.start_payment(order.id, amount)
                bank_payment.external_payment_id = start_result.external_payment_id
                bank_payment.last_error = None
            except Exception as exc:
                bank_payment.status = BankPaymentStatus.FAILED
                bank_payment.last_error = str(exc)
                payment.status = PaymentStatus.FAILED
                self.payments.save(payment)
                raise

        self._recalculate_payment_status(order)
        return self.payments.save(payment)

    def refund_payment(self, payment_id: int, amount: Decimal) -> Payment:
        """Refund part or all of a successful payment."""
        payment = self.payments.get(payment_id)
        if payment is None:
            raise PaymentNotFoundError(f"payment {payment_id} not found")

        self._refund_payment(payment, payment.order, amount)
        self._recalculate_payment_status(payment.order)
        return self.payments.save(payment)

    def sync_bank_payment(self, payment_id: int) -> Payment:
        """Sync an acquiring payment with the bank and apply the returned status."""
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
            self._deposit_payment(payment, payment.order, paid_at=result.paid_at)
        elif result.status in {BankPaymentStatus.FAILED, BankPaymentStatus.NOT_FOUND}:
            payment.status = PaymentStatus.FAILED

        self._recalculate_payment_status(payment.order)
        return self.payments.save(payment)

    def _deposit_payment(self, payment: Payment, order: Order, paid_at=None) -> None:
        """Mark a payment as succeeded after validating order availability."""
        if payment.status in {PaymentStatus.SUCCEEDED, PaymentStatus.PARTIALLY_REFUNDED, PaymentStatus.REFUNDED}:
            raise PaymentValidationError("payment is already deposited")
        if Decimal(payment.amount) <= Decimal("0.00"):
            raise PaymentValidationError("payment amount must be positive")

        available_amount = order.available_amount()
        if payment in order.payments:
            available_amount += payment.committed_amount()
        if Decimal(payment.amount) > available_amount:
            raise PaymentValidationError("payment amount exceeds order remaining amount")

        payment.status = PaymentStatus.SUCCEEDED
        payment.paid_at = paid_at or utcnow()

    def _refund_payment(self, payment: Payment, order: Order, amount: Decimal) -> None:
        """Refund part of a settled payment after validating limits."""
        if payment.status not in {PaymentStatus.SUCCEEDED, PaymentStatus.PARTIALLY_REFUNDED}:
            raise PaymentValidationError("only successful payments can be refunded")
        if amount <= Decimal("0.00"):
            raise PaymentValidationError("refund amount must be positive")

        available_refund = Decimal(payment.amount) - payment.refunded_total()
        if amount > available_refund:
            raise PaymentValidationError("refund amount exceeds refundable amount")

        payment.refunded_amount = payment.refunded_total() + amount
        payment.status = (
            PaymentStatus.REFUNDED
            if Decimal(payment.refunded_amount) == Decimal(payment.amount)
            else PaymentStatus.PARTIALLY_REFUNDED
        )

    def _recalculate_payment_status(self, order: Order) -> None:
        """Update the order payment status from its current payment totals."""
        paid = order.paid_amount()
        total = Decimal(order.total_amount)
        if paid <= Decimal("0.00"):
            order.payment_status = OrderPaymentStatus.UNPAID
        elif paid < total:
            order.payment_status = OrderPaymentStatus.PARTIALLY_PAID
        else:
            order.payment_status = OrderPaymentStatus.PAID
