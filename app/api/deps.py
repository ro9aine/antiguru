from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.repositories import OrderRepository, PaymentRepository
from app.services.payment_service import PaymentService


def get_order_repository(session: AsyncSession = Depends(get_session)) -> OrderRepository:
    """Create an order repository for the current request session."""
    return OrderRepository(session)


def get_payment_repository(session: AsyncSession = Depends(get_session)) -> PaymentRepository:
    """Create a payment repository for the current request session."""
    return PaymentRepository(session)


def get_payment_service(
    orders: OrderRepository = Depends(get_order_repository),
    payments: PaymentRepository = Depends(get_payment_repository),
) -> PaymentService:
    """Create a payment service wired to request-scoped repositories."""
    return PaymentService(orders=orders, payments=payments)
