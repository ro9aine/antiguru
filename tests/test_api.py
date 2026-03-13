from decimal import Decimal
from datetime import datetime, timezone

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from fastapi import FastAPI

from app.api.deps import get_payment_service
from app.api.routes import router
from app.db import Base, get_session
from app.domain.enums import BankPaymentStatus, OrderPaymentStatus
from app.integrations.bank_client import BankCheckResult, BankStartResult
from app.models import Order, Payment
from app.repositories import OrderRepository, PaymentRepository
from app.services.payment_service import PaymentService


class StubBankClient:
    def __init__(self) -> None:
        self.next_status = BankPaymentStatus.PAID
        self.amount = Decimal("40.00")

    async def start_payment(self, order_id: int, amount: Decimal) -> BankStartResult:
        return BankStartResult(external_payment_id=f"bank-{order_id}-{amount}")

    async def check_payment(self, external_payment_id: str) -> BankCheckResult:
        return BankCheckResult(
            external_payment_id=external_payment_id,
            amount=self.amount,
            status=self.next_status,
            paid_at=None,
        )


@pytest_asyncio.fixture
async def api_client() -> tuple[httpx.AsyncClient, OrderRepository, AsyncSession]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    orders = OrderRepository(session)
    payments = PaymentRepository(session)
    bank_client = StubBankClient()

    test_app = FastAPI()
    test_app.include_router(router)

    async def override_get_session():
        yield session

    def override_get_payment_service() -> PaymentService:
        return PaymentService(orders=orders, payments=payments, bank_client=bank_client)

    test_app.dependency_overrides[get_session] = override_get_session
    test_app.dependency_overrides[get_payment_service] = override_get_payment_service

    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client, orders, session

    await session.close()
    await engine.dispose()


@pytest.mark.asyncio
async def test_list_orders_returns_existing_orders(
    api_client: tuple[httpx.AsyncClient, OrderRepository, AsyncSession],
) -> None:
    client, orders, _session = api_client
    await orders.add(Order(total_amount=Decimal("100.00"), payment_status=OrderPaymentStatus.UNPAID))

    response = await client.get("/orders")

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": 1,
            "total_amount": "100.00",
            "paid_amount": "0.00",
            "payment_status": "unpaid",
        }
    ]


@pytest.mark.asyncio
async def test_create_cash_payment_returns_created_payment(
    api_client: tuple[httpx.AsyncClient, OrderRepository, AsyncSession],
) -> None:
    client, orders, _session = api_client
    await orders.add(Order(total_amount=Decimal("100.00"), payment_status=OrderPaymentStatus.UNPAID))

    response = await client.post(
        "/orders/1/payments",
        json={"amount": "60.00", "payment_type": "cash"},
    )

    assert response.status_code == 201
    assert response.json()["status"] == "succeeded"
    assert response.json()["refunded_amount"] == "0.00"
    assert response.json()["bank_payment"] is None


@pytest.mark.asyncio
async def test_refund_payment_returns_updated_payment(
    api_client: tuple[httpx.AsyncClient, OrderRepository, AsyncSession],
) -> None:
    client, orders, _session = api_client
    await orders.add(Order(total_amount=Decimal("100.00"), payment_status=OrderPaymentStatus.UNPAID))

    payment_response = await client.post(
        "/orders/1/payments",
        json={"amount": "100.00", "payment_type": "cash"},
    )
    payment_id = payment_response.json()["id"]

    response = await client.post(
        f"/payments/{payment_id}/refund",
        json={"amount": "30.00"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "partially_refunded"
    assert response.json()["refunded_amount"] == "30.00"


@pytest.mark.asyncio
async def test_sync_bank_payment_returns_bank_details(
    api_client: tuple[httpx.AsyncClient, OrderRepository, AsyncSession],
) -> None:
    client, orders, _session = api_client
    await orders.add(Order(total_amount=Decimal("100.00"), payment_status=OrderPaymentStatus.UNPAID))

    payment_response = await client.post(
        "/orders/1/payments",
        json={"amount": "40.00", "payment_type": "acquiring"},
    )
    payment_id = payment_response.json()["id"]

    response = await client.post(f"/payments/{payment_id}/sync-bank")

    assert response.status_code == 200
    assert response.json()["status"] == "succeeded"
    assert response.json()["bank_payment"]["status"] == "paid"
    assert response.json()["bank_payment"]["external_payment_id"] == "bank-1-40.00"


@pytest.mark.asyncio
async def test_create_payment_is_idempotent(
    api_client: tuple[httpx.AsyncClient, OrderRepository, AsyncSession],
) -> None:
    client, orders, session = api_client
    await orders.add(Order(total_amount=Decimal("100.00"), payment_status=OrderPaymentStatus.UNPAID))
    headers = {"Idempotency-Key": "create-1"}

    first_response = await client.post(
        "/orders/1/payments",
        json={"amount": "60.00", "payment_type": "cash"},
        headers=headers,
    )
    second_response = await client.post(
        "/orders/1/payments",
        json={"amount": "60.00", "payment_type": "cash"},
        headers=headers,
    )

    payment_count = await session.scalar(select(func.count()).select_from(Payment))

    assert first_response.status_code == 201
    assert second_response.status_code == 201
    assert first_response.json()["id"] == second_response.json()["id"]
    assert payment_count == 1


@pytest.mark.asyncio
async def test_create_payment_idempotency_key_conflict_returns_conflict(
    api_client: tuple[httpx.AsyncClient, OrderRepository, AsyncSession],
) -> None:
    client, orders, _session = api_client
    await orders.add(Order(total_amount=Decimal("100.00"), payment_status=OrderPaymentStatus.UNPAID))
    headers = {"Idempotency-Key": "create-conflict"}

    first_response = await client.post(
        "/orders/1/payments",
        json={"amount": "60.00", "payment_type": "cash"},
        headers=headers,
    )
    second_response = await client.post(
        "/orders/1/payments",
        json={"amount": "50.00", "payment_type": "cash"},
        headers=headers,
    )

    assert first_response.status_code == 201
    assert second_response.status_code == 409


@pytest.mark.asyncio
async def test_refund_is_idempotent(
    api_client: tuple[httpx.AsyncClient, OrderRepository, AsyncSession],
) -> None:
    client, orders, _session = api_client
    await orders.add(Order(total_amount=Decimal("100.00"), payment_status=OrderPaymentStatus.UNPAID))

    payment_response = await client.post(
        "/orders/1/payments",
        json={"amount": "100.00", "payment_type": "cash"},
    )
    payment_id = payment_response.json()["id"]
    headers = {"Idempotency-Key": "refund-1"}

    first_response = await client.post(
        f"/payments/{payment_id}/refund",
        json={"amount": "30.00"},
        headers=headers,
    )
    second_response = await client.post(
        f"/payments/{payment_id}/refund",
        json={"amount": "30.00"},
        headers=headers,
    )

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert first_response.json()["refunded_amount"] == "30.00"
    assert second_response.json()["refunded_amount"] == "30.00"


@pytest.mark.asyncio
async def test_bank_webhook_marks_acquiring_payment_as_paid(
    api_client: tuple[httpx.AsyncClient, OrderRepository, AsyncSession],
) -> None:
    client, orders, _session = api_client
    await orders.add(Order(total_amount=Decimal("100.00"), payment_status=OrderPaymentStatus.UNPAID))

    payment_response = await client.post(
        "/orders/1/payments",
        json={"amount": "40.00", "payment_type": "acquiring"},
    )

    response = await client.post(
        "/webhooks/bank/payments",
        json={
            "payment_id": "bank-1-40.00",
            "amount": "40.00",
            "status": "paid",
            "paid_at": datetime(2026, 3, 14, 12, 0, tzinfo=timezone.utc).isoformat(),
        },
    )

    assert payment_response.status_code == 201
    assert response.status_code == 200
    assert response.json()["status"] == "succeeded"
    assert response.json()["bank_payment"]["status"] == "paid"


@pytest.mark.asyncio
async def test_bank_webhook_returns_not_found_for_unknown_external_payment(
    api_client: tuple[httpx.AsyncClient, OrderRepository, AsyncSession],
) -> None:
    client, _orders, _session = api_client

    response = await client.post(
        "/webhooks/bank/payments",
        json={
            "payment_id": "missing-payment",
            "amount": "40.00",
            "status": "paid",
            "paid_at": None,
        },
    )

    assert response.status_code == 404
