class DomainError(Exception):
    pass


class OrderNotFoundError(DomainError):
    pass


class PaymentNotFoundError(DomainError):
    pass


class PaymentValidationError(DomainError):
    pass


class BankApiError(DomainError):
    pass


class IdempotencyConflictError(DomainError):
    pass
