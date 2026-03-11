from fastapi import Depends
from sqlalchemy.orm import Session

from app.db import get_session
from app.services.payment_service import PaymentService


def get_payment_service(session: Session = Depends(get_session)) -> PaymentService:
    return PaymentService(session=session)
