from fastapi import APIRouter, Depends, HTTPException, status

from app.api.schemas import OrderResponse, PaymentCreateRequest, PaymentResponse, RefundRequest
from app.api.deps import get_order_repository, get_payment_service
from app.domain.exceptions import (
    BankApiError,
    DomainError,
    OrderNotFoundError,
    PaymentNotFoundError,
)
from app.repositories import OrderRepository
from app.services.payment_service import PaymentService

router = APIRouter()


@router.get("/orders", response_model=list[OrderResponse])
def list_orders(orders_repo: OrderRepository = Depends(get_order_repository)) -> list[OrderResponse]:
    orders = orders_repo.list()
    return [OrderResponse.from_model(order) for order in orders]


@router.post("/orders/{order_id}/payments", response_model=PaymentResponse, status_code=status.HTTP_201_CREATED)
def create_payment(
    order_id: int,
    payload: PaymentCreateRequest,
    service: PaymentService = Depends(get_payment_service),
) -> PaymentResponse:
    try:
        payment = service.create_payment(order_id, payload.amount, payload.payment_type)
        return PaymentResponse.from_model(payment)
    except BankApiError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except OrderNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except DomainError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/payments/{payment_id}/refund", response_model=PaymentResponse)
def refund_payment(
    payment_id: int,
    payload: RefundRequest,
    service: PaymentService = Depends(get_payment_service),
) -> PaymentResponse:
    try:
        payment = service.refund_payment(payment_id, payload.amount)
        return PaymentResponse.from_model(payment)
    except PaymentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except DomainError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/payments/{payment_id}/sync-bank", response_model=PaymentResponse)
def sync_bank_payment(payment_id: int, service: PaymentService = Depends(get_payment_service)) -> PaymentResponse:
    try:
        payment = service.sync_bank_payment(payment_id)
        return PaymentResponse.from_model(payment)
    except BankApiError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except PaymentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except DomainError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
