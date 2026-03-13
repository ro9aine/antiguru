from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.domain.enums import BankPaymentStatus, OrderPaymentStatus, PaymentStatus, PaymentType
from app.integrations.bank_client import BankCheckResult, BankStartResult
from app.models import Order
from app.repositories import OrderRepository, PaymentRepository
from app.services.payment_service import PaymentService


class StubBankClient:
    def __init__(self) -> None:
        self.next_status = BankPaymentStatus.PAID

    async def start_payment(self, order_id: int, amount: Decimal) -> BankStartResult:
        return BankStartResult(external_payment_id=f"bank-{order_id}-{amount}")

    async def check_payment(self, external_payment_id: str) -> BankCheckResult:
        return BankCheckResult(
            external_payment_id=external_payment_id,
            amount=Decimal("40.00"),
            status=self.next_status,
            paid_at=None,
        )


async def build_session() -> AsyncSession:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    return factory()


@pytest.mark.asyncio
async def test_cash_payment_changes_order_status() -> None:
    session = await build_session()
    order = Order(total_amount=Decimal("100.00"), payment_status=OrderPaymentStatus.UNPAID)
    orders = OrderRepository(session)
    payments = PaymentRepository(session)
    await orders.add(order)

    service = PaymentService(orders=orders, payments=payments)
    payment = await service.create_payment(order.id, Decimal("60.00"), PaymentType.CASH)

    assert payment.status == PaymentStatus.SUCCEEDED
    assert payment.order.payment_status == OrderPaymentStatus.PARTIALLY_PAID
    await session.close()


@pytest.mark.asyncio
async def test_refund_recalculates_order_status() -> None:
    session = await build_session()
    order = Order(total_amount=Decimal("100.00"), payment_status=OrderPaymentStatus.UNPAID)
    orders = OrderRepository(session)
    payments = PaymentRepository(session)
    await orders.add(order)

    service = PaymentService(orders=orders, payments=payments)
    payment = await service.create_payment(order.id, Decimal("100.00"), PaymentType.CASH)
    refunded = await service.refund_payment(payment.id, Decimal("30.00"))

    assert refunded.status == PaymentStatus.PARTIALLY_REFUNDED
    assert refunded.order.payment_status == OrderPaymentStatus.PARTIALLY_PAID
    await session.close()


@pytest.mark.asyncio
async def test_acquiring_payment_becomes_paid_after_sync() -> None:
    session = await build_session()
    order = Order(total_amount=Decimal("100.00"), payment_status=OrderPaymentStatus.UNPAID)
    orders = OrderRepository(session)
    payments = PaymentRepository(session)
    await orders.add(order)

    service = PaymentService(orders=orders, payments=payments, bank_client=StubBankClient())
    payment = await service.create_payment(order.id, Decimal("40.00"), PaymentType.ACQUIRING)

    assert payment.status == PaymentStatus.PENDING
    synced = await service.sync_bank_payment(payment.id)

    assert synced.status == PaymentStatus.SUCCEEDED
    assert synced.order.payment_status == OrderPaymentStatus.PARTIALLY_PAID
    await session.close()


@pytest.mark.asyncio
async def test_pending_acquiring_reserves_order_amount() -> None:
    session = await build_session()
    order = Order(total_amount=Decimal("100.00"), payment_status=OrderPaymentStatus.UNPAID)
    orders = OrderRepository(session)
    payments = PaymentRepository(session)
    await orders.add(order)

    service = PaymentService(orders=orders, payments=payments, bank_client=StubBankClient())
    await service.create_payment(order.id, Decimal("80.00"), PaymentType.ACQUIRING)

    try:
        await service.create_payment(order.id, Decimal("30.00"), PaymentType.CASH)
    except Exception as exc:
        assert "exceeds order remaining amount" in str(exc)
    else:
        raise AssertionError("second payment should not be allowed")
    await session.close()
