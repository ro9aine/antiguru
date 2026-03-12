from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_payment_service
from app.api.routes import router
from app.db import Base, get_session
from app.domain.enums import BankPaymentStatus, OrderPaymentStatus
from app.integrations.bank_client import BankCheckResult, BankStartResult
from app.models import Order
from app.repositories import OrderRepository, PaymentRepository
from app.services.payment_service import PaymentService


class StubBankClient:
    def __init__(self) -> None:
        self.next_status = BankPaymentStatus.PAID
        self.amount = Decimal("40.00")

    def start_payment(self, order_id: int, amount: Decimal) -> BankStartResult:
        return BankStartResult(external_payment_id=f"bank-{order_id}-{amount}")

    def check_payment(self, external_payment_id: str) -> BankCheckResult:
        return BankCheckResult(
            external_payment_id=external_payment_id,
            amount=self.amount,
            status=self.next_status,
            paid_at=None,
        )


@pytest.fixture
def api_client() -> tuple[TestClient, OrderRepository]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    orders = OrderRepository(session)
    payments = PaymentRepository(session)
    bank_client = StubBankClient()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        yield session

    def override_get_payment_service() -> PaymentService:
        return PaymentService(orders=orders, payments=payments, bank_client=bank_client)

    test_app.dependency_overrides[get_session] = override_get_session
    test_app.dependency_overrides[get_payment_service] = override_get_payment_service

    with TestClient(test_app) as client:
        yield client, orders

    session.close()


def test_list_orders_returns_existing_orders(api_client: tuple[TestClient, OrderRepository]) -> None:
    client, orders = api_client
    orders.add(Order(total_amount=Decimal("100.00"), payment_status=OrderPaymentStatus.UNPAID))

    response = client.get("/orders")

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": 1,
            "total_amount": "100.00",
            "paid_amount": "0.00",
            "payment_status": "unpaid",
        }
    ]


def test_create_cash_payment_returns_created_payment(api_client: tuple[TestClient, OrderRepository]) -> None:
    client, orders = api_client
    orders.add(Order(total_amount=Decimal("100.00"), payment_status=OrderPaymentStatus.UNPAID))

    response = client.post(
        "/orders/1/payments",
        json={"amount": "60.00", "payment_type": "cash"},
    )

    assert response.status_code == 201
    assert response.json()["status"] == "succeeded"
    assert response.json()["refunded_amount"] == "0.00"
    assert response.json()["bank_payment"] is None


def test_refund_payment_returns_updated_payment(api_client: tuple[TestClient, OrderRepository]) -> None:
    client, orders = api_client
    orders.add(Order(total_amount=Decimal("100.00"), payment_status=OrderPaymentStatus.UNPAID))

    payment_response = client.post(
        "/orders/1/payments",
        json={"amount": "100.00", "payment_type": "cash"},
    )
    payment_id = payment_response.json()["id"]

    response = client.post(
        f"/payments/{payment_id}/refund",
        json={"amount": "30.00"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "partially_refunded"
    assert response.json()["refunded_amount"] == "30.00"


def test_sync_bank_payment_returns_bank_details(api_client: tuple[TestClient, OrderRepository]) -> None:
    client, orders = api_client
    orders.add(Order(total_amount=Decimal("100.00"), payment_status=OrderPaymentStatus.UNPAID))

    payment_response = client.post(
        "/orders/1/payments",
        json={"amount": "40.00", "payment_type": "acquiring"},
    )
    payment_id = payment_response.json()["id"]

    response = client.post(f"/payments/{payment_id}/sync-bank")

    assert response.status_code == 200
    assert response.json()["status"] == "succeeded"
    assert response.json()["bank_payment"]["status"] == "paid"
    assert response.json()["bank_payment"]["external_payment_id"] == "bank-1-40.00"
