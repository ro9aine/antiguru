from enum import StrEnum


class OrderPaymentStatus(StrEnum):
    UNPAID = "unpaid"
    PARTIALLY_PAID = "partially_paid"
    PAID = "paid"


class PaymentType(StrEnum):
    CASH = "cash"
    ACQUIRING = "acquiring"


class PaymentStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    PARTIALLY_REFUNDED = "partially_refunded"
    REFUNDED = "refunded"
    FAILED = "failed"


class BankPaymentStatus(StrEnum):
    NEW = "new"
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"
    NOT_FOUND = "not_found"

