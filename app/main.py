from decimal import Decimal

from fastapi import FastAPI
from sqlalchemy import select

from app.api.routes import router
from app.db import Base, SessionLocal, engine
from app.domain.enums import OrderPaymentStatus
from app.models import Order

app = FastAPI(title="Payment Service")
app.include_router(router)


@app.on_event("startup")
def on_startup() -> None:
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        has_orders = session.scalar(select(Order.id).limit(1))
        if not has_orders:
            session.add_all(
                [
                    Order(total_amount=Decimal("1000.00"), payment_status=OrderPaymentStatus.UNPAID),
                    Order(total_amount=Decimal("2500.00"), payment_status=OrderPaymentStatus.UNPAID),
                ]
            )
            session.commit()
    finally:
        session.close()
