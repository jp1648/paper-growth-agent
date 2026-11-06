from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, WithJsonSchema, field_validator, model_validator

D = Decimal
ZERO = D("0")
PAPER_URL = "https://paper-api.alpaca.markets"
TERMINAL = {"filled", "canceled", "expired", "rejected", "replaced"}


def now() -> datetime:
    return datetime.now(UTC)


def dt(value: str | datetime) -> datetime:
    value = datetime.fromisoformat(value) if isinstance(value, str) else value
    if value.tzinfo is None:
        raise ValueError("Timezone required")
    return value


def dec(value) -> Decimal:
    result = D(str(value))
    if not result.is_finite():
        raise ValueError("Non-finite decimal")
    return result


def dumps(value) -> str:
    return json.dumps(
        value,
        default=lambda x: (
            x.model_dump(mode="json")
            if isinstance(x, BaseModel)
            else x.isoformat()
            if isinstance(x, datetime)
            else str(x)
        ),
        sort_keys=True,
    )


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, revalidate_instances="always")


class Decision(Model):
    symbol: str = Field(pattern=r"^[A-Z][A-Z0-9.\-]{0,9}$")
    action: Literal["BUY", "ADD", "HOLD", "REDUCE", "SELL", "WATCH"]
    quantity: Annotated[Decimal, WithJsonSchema({"type": "integer", "minimum": 0})] = Field(
        ge=0, description="Whole shares. Use numeric 0 for WATCH or HOLD."
    )
    limit_price: Annotated[Decimal, WithJsonSchema({"type": "number", "minimum": 0})] = Field(
        ge=0,
        description="USD per share, with at most two decimal places. Use numeric 0 for WATCH or HOLD; never N/A or text.",
    )
    business_attraction: str = Field(min_length=1)
    price_attraction: str = Field(min_length=1)
    why_now: str = Field(min_length=1)
    catalysts: list[str] = Field(min_length=1)
    thesis_development: str = Field(min_length=1)
    holding_period_days: int = Field(ge=1, le=730)
    next_review: datetime
    add_if: str = Field(min_length=1)
    reduce_if: str = Field(min_length=1)
    exit_if: str = Field(min_length=1)
    exit_reason: Literal[
        "none", "thesis_invalid", "valuation", "catalyst_failed", "better_opportunity", "risk"
    ]
    evidence_ids: list[str] = Field(min_length=1)
    assumptions: list[str]
    expected_outcome: str = Field(min_length=1)
    confidence: Annotated[Decimal, WithJsonSchema({"type": "number", "minimum": 0, "maximum": 1})] = Field(
        ge=0, le=1
    )

    @field_validator("next_review")
    @classmethod
    def aware(cls, value):
        return dt(value)

    @model_validator(mode="after")
    def valid_trade(self):
        if self.quantity != self.quantity.to_integral_value():
            raise ValueError("Whole shares required")
        if self.action in {"BUY", "ADD", "REDUCE", "SELL"}:
            if self.quantity <= 0 or self.limit_price < 1:
                raise ValueError("Trade needs positive quantity and price >= $1")
            if self.limit_price != self.limit_price.quantize(D(".01")):
                raise ValueError("Limit price must be in cents")
        elif self.quantity != 0:
            raise ValueError("HOLD/WATCH cannot carry an order quantity")
        return self

    @property
    def side(self):
        return "buy" if self.action in {"BUY", "ADD"} else "sell"


class Quote(Model):
    symbol: str
    bid: Decimal = Field(gt=0)
    ask: Decimal = Field(gt=0)
    timestamp: datetime
    source: str
    feed: str
    coverage: str
    delayed: bool
    halt_checked_at: datetime
    halted: bool | None

    @field_validator("timestamp", "halt_checked_at")
    @classmethod
    def aware(cls, value):
        return dt(value)


class Snapshot(Model):
    account: dict
    positions: list[dict]
    orders: list[dict]
    activities: list[dict]
    captured_at: datetime


class PaperBroker(Protocol):
    def account(self) -> dict: ...
    def open_orders(self) -> list[dict]: ...
    def snapshot(self, after: str, tracked_ids: list[str]) -> Snapshot: ...
    def lookup(self, client_id: str) -> dict | None: ...
    def submit(self, payload: dict) -> dict: ...
    def cancel(self, order_id: str) -> None: ...
    def clock(self) -> dict: ...
    def calendar(self, start: str, end: str) -> list[dict]: ...
    def asset(self, symbol: str) -> dict: ...


class MarketData(Protocol):
    def quote(self, symbol: str) -> Quote: ...
    def news(self, symbol: str, since: str) -> list[dict]: ...
    def bars(self, symbol: str, start: str, end: str) -> list[dict]: ...
    def session_close(self, symbol: str, day: dict) -> Decimal: ...
