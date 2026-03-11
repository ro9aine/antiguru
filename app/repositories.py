from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models import Order, Payment


class OrderRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, order_id: int) -> Order | None:
        query = select(Order).where(Order.id == order_id).options(joinedload(Order.payments))
        return self.session.scalar(query)

    def list(self) -> list[Order]:
        query = select(Order).options(joinedload(Order.payments)).order_by(Order.id)
        return list(self.session.scalars(query).unique())


class PaymentRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, payment_id: int) -> Payment | None:
        query = (
            select(Payment)
            .where(Payment.id == payment_id)
            .options(joinedload(Payment.order).joinedload(Order.payments), joinedload(Payment.bank_payment))
        )
        return self.session.scalar(query)

    def add(self, payment: Payment) -> None:
        self.session.add(payment)

