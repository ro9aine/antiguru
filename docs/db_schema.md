# DB Schema

## Таблицы

### `orders`

- `id` PK
- `total_amount` numeric(12, 2)
- `payment_status` enum(`unpaid`, `partially_paid`, `paid`)
- `created_at` timestamptz

### `payments`

- `id` PK
- `order_id` FK -> `orders.id`
- `payment_type` enum(`cash`, `acquiring`)
- `amount` numeric(12, 2)
- `refunded_amount` numeric(12, 2)
- `status` enum(`pending`, `succeeded`, `partially_refunded`, `refunded`, `failed`)
- `created_at` timestamptz
- `paid_at` timestamptz nullable

### `bank_payments`

- `id` PK
- `payment_id` FK -> `payments.id`, unique
- `external_payment_id` varchar(128), unique, nullable
- `status` enum(`new`, `pending`, `paid`, `failed`, `not_found`)
- `last_error` varchar(255), nullable
- `last_synced_at` timestamptz, nullable
- `paid_at` timestamptz, nullable
- `created_at` timestamptz

### `idempotency_keys`

- `id` PK
- `operation` enum(`create_payment`, `refund_payment`)
- `key` varchar(128)
- `request_fingerprint` varchar(255)
- `payment_id` FK -> `payments.id`
- `created_at` timestamptz
- unique(`operation`, `key`)

## Связи

- Один `order` имеет много `payments`
- Один `payment` типа `acquiring` может иметь одну запись `bank_payment`

## Принципы согласования статусов

- `orders.payment_status` не хранит независимое состояние, а является производным от суммы успешных платежей с учетом возвратов.
- `bank_payments.status` хранит последнее известное внешнее состояние.
- `payments.status` хранит внутреннее состояние операции в приложении.
