from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from app.domain.enums import BankPaymentStatus, IdempotencyOperation, OrderPaymentStatus, PaymentStatus, PaymentType
from app.domain.exceptions import (
    IdempotencyConflictError,
    OrderNotFoundError,
    PaymentNotFoundError,
    PaymentValidationError,
)
from app.integrations.bank_client import BankApiClient, BankCheckResult
from app.models import BankPayment, IdempotencyKey, Order, Payment, utcnow
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

    async def create_payment(
        self,
        order_id: int,
        amount: Decimal,
        payment_type: PaymentType,
        idempotency_key: str | None = None,
    ) -> Payment:
        """Create a payment, optionally opening an acquiring payment with the bank."""
        request_fingerprint = self._create_payment_fingerprint(order_id, amount, payment_type)
        existing_payment = await self._get_idempotent_payment(
            operation=IdempotencyOperation.CREATE_PAYMENT,
            key=idempotency_key,
            request_fingerprint=request_fingerprint,
        )
        if existing_payment is not None:
            return existing_payment

        order = await self.orders.get(order_id)
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
            return await self._save_payment_with_order_updates(
                payment=payment,
                order=order,
                operation=IdempotencyOperation.CREATE_PAYMENT,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
            )

        bank_payment = BankPayment(payment=payment, status=BankPaymentStatus.PENDING)
        payment.bank_payment = bank_payment
        self.payments.add_bank_payment(bank_payment)
        payment = await self._save_payment_with_order_updates(
            payment=payment,
            order=order,
            operation=IdempotencyOperation.CREATE_PAYMENT,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
        )
        persisted_bank_payment = payment.bank_payment
        if persisted_bank_payment is None:
            raise RuntimeError(f"acquiring payment {payment.id} lost its bank payment state")

        try:
            start_result = await self.bank_client.start_payment(order.id, amount)
        except Exception as exc:
            persisted_bank_payment.status = BankPaymentStatus.FAILED
            persisted_bank_payment.last_error = str(exc)
            payment.status = PaymentStatus.FAILED
            self._recalculate_payment_status(payment.order)
            await self.payments.save(payment)
            raise

        persisted_bank_payment.external_payment_id = start_result.external_payment_id
        persisted_bank_payment.last_error = None
        return await self.payments.save(payment)

    async def refund_payment(self, payment_id: int, amount: Decimal, idempotency_key: str | None = None) -> Payment:
        """Refund part or all of a successful payment."""
        request_fingerprint = self._refund_payment_fingerprint(payment_id, amount)
        existing_payment = await self._get_idempotent_payment(
            operation=IdempotencyOperation.REFUND_PAYMENT,
            key=idempotency_key,
            request_fingerprint=request_fingerprint,
        )
        if existing_payment is not None:
            return existing_payment

        payment = await self.payments.get(payment_id)
        if payment is None:
            raise PaymentNotFoundError(f"payment {payment_id} not found")

        self._refund_payment(payment, payment.order, amount)
        return await self._save_payment_with_order_updates(
            payment=payment,
            order=payment.order,
            operation=IdempotencyOperation.REFUND_PAYMENT,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
        )

    async def sync_bank_payment(self, payment_id: int) -> Payment:
        """Sync an acquiring payment with the bank and apply the returned status."""
        payment = await self.payments.get(payment_id)
        if payment is None:
            raise PaymentNotFoundError(f"payment {payment_id} not found")
        bank_payment = payment.bank_payment
        if payment.payment_type != PaymentType.ACQUIRING or bank_payment is None:
            raise PaymentValidationError("payment is not an acquiring payment")
        if not bank_payment.external_payment_id:
            raise PaymentValidationError("bank payment does not have external id")

        result = await self.bank_client.check_payment(bank_payment.external_payment_id)
        return await self._apply_bank_result(payment, result)

    async def handle_bank_webhook(
        self,
        external_payment_id: str,
        amount: Decimal,
        status: BankPaymentStatus,
        paid_at: datetime | None,
    ) -> Payment:
        """Apply a bank webhook payload to the matching acquiring payment."""
        payment = await self.payments.get_by_external_payment_id(external_payment_id)
        if payment is None:
            raise PaymentNotFoundError(f"payment with external id {external_payment_id} not found")

        return await self._apply_bank_result(
            payment,
            BankCheckResult(
                external_payment_id=external_payment_id,
                amount=amount,
                status=status,
                paid_at=paid_at,
            ),
        )

    def _deposit_payment(self, payment: Payment, order: Order, paid_at: datetime | None = None) -> None:
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

    async def _get_idempotent_payment(
        self,
        operation: IdempotencyOperation,
        key: str | None,
        request_fingerprint: str,
    ) -> Payment | None:
        if not key:
            return None

        record = await self.payments.get_by_idempotency_key(operation=operation, key=key)
        if record is None:
            return None
        if record.request_fingerprint != request_fingerprint:
            raise IdempotencyConflictError("idempotency key is already used with a different request payload")
        return record.payment

    def _register_idempotency_key(
        self,
        operation: IdempotencyOperation,
        key: str | None,
        request_fingerprint: str,
        payment: Payment,
    ) -> None:
        if not key:
            return

        self.payments.add_idempotency_key(
            IdempotencyKey(
                operation=operation,
                key=key,
                request_fingerprint=request_fingerprint,
                payment=payment,
            )
        )

    async def _apply_bank_result(self, payment: Payment, result: BankCheckResult) -> Payment:
        if payment.payment_type != PaymentType.ACQUIRING or payment.bank_payment is None:
            raise PaymentValidationError("payment is not an acquiring payment")

        bank_payment = payment.bank_payment
        bank_payment.status = result.status
        bank_payment.last_synced_at = utcnow()
        bank_payment.paid_at = result.paid_at
        bank_payment.last_error = None if result.status != BankPaymentStatus.NOT_FOUND else "payment not found"

        if result.status == BankPaymentStatus.PAID and payment.status == PaymentStatus.PENDING:
            if result.amount != payment.amount:
                raise PaymentValidationError("bank payment amount does not match internal payment amount")
            self._deposit_payment(payment, payment.order, paid_at=result.paid_at)
        elif result.status in {BankPaymentStatus.FAILED, BankPaymentStatus.NOT_FOUND}:
            payment.status = PaymentStatus.FAILED

        self._recalculate_payment_status(payment.order)
        return await self.payments.save(payment)

    async def _save_payment_with_order_updates(
        self,
        payment: Payment,
        order: Order,
        operation: IdempotencyOperation,
        idempotency_key: str | None,
        request_fingerprint: str,
    ) -> Payment:
        self._recalculate_payment_status(order)
        self._register_idempotency_key(
            operation=operation,
            key=idempotency_key,
            request_fingerprint=request_fingerprint,
            payment=payment,
        )
        return await self.payments.save(payment)

    def _create_payment_fingerprint(self, order_id: int, amount: Decimal, payment_type: PaymentType) -> str:
        return f"order:{order_id}|amount:{amount}|payment_type:{payment_type.value}"

    def _refund_payment_fingerprint(self, payment_id: int, amount: Decimal) -> str:
        return f"payment:{payment_id}|amount:{amount}"
