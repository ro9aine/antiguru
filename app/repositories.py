from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models import BankPayment, Order, Payment


class OrderRepository:
    """Provides database access helpers for order aggregates."""

    def __init__(self, session: Session) -> None:
        """Store the SQLAlchemy session used for order queries and writes."""
        self.session = session

    def get(self, order_id: int) -> Order | None:
        """Return one order with its payments preloaded."""
        query = select(Order).where(Order.id == order_id).options(joinedload(Order.payments))
        return self.session.scalar(query)

    def list(self) -> list[Order]:
        """Return all orders sorted by identifier with payments preloaded."""
        query = select(Order).options(joinedload(Order.payments)).order_by(Order.id)
        return list(self.session.scalars(query).unique())

    def add(self, order: Order) -> Order:
        """Persist an order and reload it from the database."""
        self.session.add(order)
        self.session.commit()
        self.session.refresh(order)
        return order


class PaymentRepository:
    """Provides database access helpers for payments and bank payment records."""

    def __init__(self, session: Session) -> None:
        """Store the SQLAlchemy session used for payment queries and writes."""
        self.session = session

    def get(self, payment_id: int) -> Payment | None:
        """Return one payment with its order and bank payment preloaded."""
        query = (
            select(Payment)
            .where(Payment.id == payment_id)
            .options(joinedload(Payment.order).joinedload(Order.payments), joinedload(Payment.bank_payment))
        )
        return self.session.scalar(query)

    def add(self, payment: Payment) -> None:
        """Stage a payment entity for persistence in the current unit of work."""
        self.session.add(payment)

    def add_bank_payment(self, bank_payment: BankPayment) -> None:
        """Stage a bank payment entity for persistence in the current unit of work."""
        self.session.add(bank_payment)

    def save(self, payment: Payment) -> Payment:
        """Commit the current transaction and refresh the payment entity."""
        self.session.commit()
        self.session.refresh(payment)
        return payment
