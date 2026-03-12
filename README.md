# Payment Service Test Task

Basic payment service for order payments with cash and acquiring flows.

## Stack

- `FastAPI` for the HTTP API
- `SQLAlchemy` for ORM and persistence
- `SQLite` by default for local development
- `httpx` for bank API integration
- `pytest` for tests

## Architecture

The project is split into the following layers:

- `app/domain` contains enums and domain exceptions
- `app/models` contains ORM models and read-side calculation helpers
- `app/repositories` contains persistence access for orders and payments
- `app/services` contains payment workflows and state transitions
- `app/integrations` contains the external bank API client
- `app/api` contains request/response schemas and HTTP routes

Current responsibility split:

- `PaymentService` owns payment state transitions such as deposit, refund, and order status recalculation
- repositories own session-backed persistence operations
- ORM models are kept focused on data shape and derived read values

## Quick Start

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install poetry
poetry install --with dev
poetry run dev
```

To run the server without the Poetry script:

```powershell
poetry run uvicorn app.main:app --reload
```

## Tests

```powershell
poetry run pytest -q
```

## Endpoints

- `GET /orders`
- `POST /orders/{order_id}/payments`
- `POST /payments/{payment_id}/refund`
- `POST /payments/{payment_id}/sync-bank`

## Database Schema

See [docs/db_schema.md](docs/db_schema.md).

## Notes

- Order creation is intentionally not implemented; orders are assumed to already exist.
- On startup, the app seeds two example orders when the orders table is empty.
