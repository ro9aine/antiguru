from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.domain.enums import IdempotencyOperation
from app.models import BankPayment, IdempotencyKey, Order, Payment


class OrderRepository:
    """Provides database access helpers for order aggregates."""

    def __init__(self, session: AsyncSession) -> None:
        """Store the SQLAlchemy session used for order queries and writes."""
        self.session = session

    async def get(self, order_id: int) -> Order | None:
        """Return one order with its payments preloaded."""
        query = select(Order).where(Order.id == order_id).options(joinedload(Order.payments))
        return await self.session.scalar(query)

    async def list(self) -> list[Order]:
        """Return all orders sorted by identifier with payments preloaded."""
        query = select(Order).options(joinedload(Order.payments)).order_by(Order.id)
        return list((await self.session.scalars(query)).unique())

    async def add(self, order: Order) -> Order:
        """Persist an order and reload it from the database."""
        self.session.add(order)
        await self.session.commit()
        await self.session.refresh(order)
        return order


class PaymentRepository:
    """Provides database access helpers for payments and bank payment records."""

    def __init__(self, session: AsyncSession) -> None:
        """Store the SQLAlchemy session used for payment queries and writes."""
        self.session = session

    async def get(self, payment_id: int) -> Payment | None:
        """Return one payment with its order and bank payment preloaded."""
        query = (
            select(Payment)
            .where(Payment.id == payment_id)
            .options(joinedload(Payment.order).joinedload(Order.payments), joinedload(Payment.bank_payment))
        )
        return await self.session.scalar(query)

    async def get_by_external_payment_id(self, external_payment_id: str) -> Payment | None:
        """Return one payment by bank external identifier with its aggregate preloaded."""
        query = (
            select(Payment)
            .join(Payment.bank_payment)
            .where(BankPayment.external_payment_id == external_payment_id)
            .options(joinedload(Payment.order).joinedload(Order.payments), joinedload(Payment.bank_payment))
        )
        return await self.session.scalar(query)

    def add(self, payment: Payment) -> None:
        """Stage a payment entity for persistence in the current unit of work."""
        self.session.add(payment)

    def add_bank_payment(self, bank_payment: BankPayment) -> None:
        """Stage a bank payment entity for persistence in the current unit of work."""
        self.session.add(bank_payment)

    async def get_by_idempotency_key(self, operation: IdempotencyOperation, key: str) -> IdempotencyKey | None:
        """Return one idempotency key with its payment aggregate eagerly loaded."""
        query = (
            select(IdempotencyKey)
            .where(IdempotencyKey.operation == operation, IdempotencyKey.key == key)
            .options(
                joinedload(IdempotencyKey.payment).joinedload(Payment.order).joinedload(Order.payments),
                joinedload(IdempotencyKey.payment).joinedload(Payment.bank_payment),
            )
        )
        return await self.session.scalar(query)

    def add_idempotency_key(self, idempotency_key: IdempotencyKey) -> None:
        """Stage an idempotency key in the current unit of work."""
        self.session.add(idempotency_key)

    async def save(self, payment: Payment) -> Payment:
        """Commit and return the payment with related order and bank state eagerly loaded."""
        await self.session.commit()
        saved_payment = await self.get(payment.id)
        if saved_payment is None:
            raise RuntimeError(f"payment {payment.id} disappeared after commit")
        return saved_payment
