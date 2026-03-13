from typing import NoReturn

from fastapi import APIRouter, Depends, Header, HTTPException, status

from app.api.schemas import BankWebhookRequest, OrderResponse, PaymentCreateRequest, PaymentResponse, RefundRequest
from app.api.deps import get_order_repository, get_payment_service
from app.domain.exceptions import (
    BankApiError,
    DomainError,
    IdempotencyConflictError,
    OrderNotFoundError,
    PaymentNotFoundError,
)
from app.repositories import OrderRepository
from app.services.payment_service import PaymentService

router = APIRouter()


def _raise_http_exception(exc: Exception) -> NoReturn:
    if isinstance(exc, BankApiError):
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    if isinstance(exc, (OrderNotFoundError, PaymentNotFoundError)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if isinstance(exc, IdempotencyConflictError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if isinstance(exc, DomainError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    raise exc


@router.get("/orders", response_model=list[OrderResponse])
async def list_orders(orders_repo: OrderRepository = Depends(get_order_repository)) -> list[OrderResponse]:
    orders = await orders_repo.list()
    return [OrderResponse.from_model(order) for order in orders]


@router.post("/orders/{order_id}/payments", response_model=PaymentResponse, status_code=status.HTTP_201_CREATED)
async def create_payment(
    order_id: int,
    payload: PaymentCreateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    service: PaymentService = Depends(get_payment_service),
) -> PaymentResponse:
    try:
        payment = await service.create_payment(order_id, payload.amount, payload.payment_type, idempotency_key)
        return PaymentResponse.from_model(payment)
    except (BankApiError, OrderNotFoundError, IdempotencyConflictError, DomainError) as exc:
        _raise_http_exception(exc)


@router.post("/payments/{payment_id}/refund", response_model=PaymentResponse)
async def refund_payment(
    payment_id: int,
    payload: RefundRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    service: PaymentService = Depends(get_payment_service),
) -> PaymentResponse:
    try:
        payment = await service.refund_payment(payment_id, payload.amount, idempotency_key)
        return PaymentResponse.from_model(payment)
    except (PaymentNotFoundError, IdempotencyConflictError, DomainError) as exc:
        _raise_http_exception(exc)


@router.post("/payments/{payment_id}/sync-bank", response_model=PaymentResponse)
async def sync_bank_payment(payment_id: int, service: PaymentService = Depends(get_payment_service)) -> PaymentResponse:
    try:
        payment = await service.sync_bank_payment(payment_id)
        return PaymentResponse.from_model(payment)
    except (BankApiError, PaymentNotFoundError, DomainError) as exc:
        _raise_http_exception(exc)


@router.post("/webhooks/bank/payments", response_model=PaymentResponse)
async def bank_payment_webhook(
    payload: BankWebhookRequest,
    service: PaymentService = Depends(get_payment_service),
) -> PaymentResponse:
    try:
        payment = await service.handle_bank_webhook(
            external_payment_id=payload.payment_id,
            amount=payload.amount,
            status=payload.status,
            paid_at=payload.paid_at,
        )
        return PaymentResponse.from_model(payment)
    except (PaymentNotFoundError, DomainError) as exc:
        _raise_http_exception(exc)
