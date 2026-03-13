from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

import httpx

from app.config import settings
from app.domain.enums import BankPaymentStatus
from app.domain.exceptions import BankApiError


@dataclass(slots=True)
class BankStartResult:
    external_payment_id: str


@dataclass(slots=True)
class BankCheckResult:
    external_payment_id: str
    amount: Decimal
    status: BankPaymentStatus
    paid_at: datetime | None


class BankApiClient:
    def __init__(self, base_url: str | None = None, timeout: float | None = None) -> None:
        self.base_url = base_url or settings.bank_api_base_url
        self.timeout = timeout or settings.bank_timeout_seconds

    async def start_payment(self, order_id: int, amount: Decimal) -> BankStartResult:
        payload = {"order_id": order_id, "amount": str(amount)}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/acquiring_start",
                    json=payload,
                )
                response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise BankApiError("bank start request timed out") from exc
        except httpx.HTTPError as exc:
            raise BankApiError("bank start request failed") from exc

        data = response.json()
        if "error" in data:
            raise BankApiError(data["error"])

        payment_id = data.get("payment_id")
        if not payment_id:
            raise BankApiError("bank start response does not contain payment_id")

        return BankStartResult(external_payment_id=str(payment_id))

    async def check_payment(self, external_payment_id: str) -> BankCheckResult:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/acquiring_check",
                    json={"payment_id": external_payment_id},
                )
                response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise BankApiError("bank check request timed out") from exc
        except httpx.HTTPError as exc:
            raise BankApiError("bank check request failed") from exc

        data = response.json()
        if data.get("error") == "payment not found":
            return BankCheckResult(
                external_payment_id=external_payment_id,
                amount=Decimal("0.00"),
                status=BankPaymentStatus.NOT_FOUND,
                paid_at=None,
            )
        if "error" in data:
            raise BankApiError(data["error"])

        return BankCheckResult(
            external_payment_id=str(data["payment_id"]),
            amount=Decimal(str(data["amount"])),
            status=BankPaymentStatus(str(data["status"])),
            paid_at=datetime.fromisoformat(data["paid_at"]) if data.get("paid_at") else None,
        )
