from __future__ import annotations

import os
import tomllib
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .models import PAPER_URL, Model


class Security(Model):
    sector: str = Field(min_length=1)
    cik: str = Field(pattern=r"^\d{1,10}$")
    kind: Literal["common_stock"] = "common_stock"


class ResearchModel(Model):
    model: str = Field(min_length=1)
    reasoning_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    input_cost_per_million: Decimal = Field(ge=0)
    output_cost_per_million: Decimal = Field(ge=0)


class Settings(Model):
    broker_url: str = PAPER_URL
    database: str = "state/ledger.sqlite"
    control_dir: str = "~/.local/state/paper-growth-agent"
    initial_capital: Decimal = Field(default=Decimal(100000), gt=0)
    experiment_weeks: int = Field(default=12, ge=1)
    max_position: Decimal = Field(default=Decimal("0.10"), gt=0, le=1)
    max_sector: Decimal = Field(default=Decimal("0.40"), gt=0, le=1)
    max_positions: int = Field(default=10, ge=1)
    max_cumulative_loss_fraction: Decimal = Field(default=Decimal("0.10"), gt=0, le=1)
    max_daily_loss_fraction: Decimal = Field(default=Decimal("0.02"), gt=0, le=1)
    turnover_30d: Decimal = Field(default=Decimal("1.0"), gt=0)
    cooldown_hours: int = Field(default=24, ge=0)
    price_tolerance: Decimal = Field(default=Decimal("0.005"), ge=0, le=Decimal("0.05"))
    max_spread_fraction: Decimal = Field(default=Decimal("0.005"), gt=0, le=Decimal("0.05"))
    max_quote_participation: Decimal = Field(default=Decimal("0.10"), gt=0, le=1)
    max_order_equity: Decimal = Field(default=Decimal("0.02"), gt=0, le=1)
    fee_buffer: Decimal = Field(default=Decimal(1), ge=0)
    quote_max_age_seconds: int = Field(default=60, ge=1, le=300)
    halt_max_age_seconds: int = Field(default=180, ge=1, le=600)
    order_ttl_minutes: int = Field(default=30, ge=1, le=390)
    time_in_force: Literal["day", "ioc"] = "day"
    feed: Literal["iex", "sip"] = "iex"
    poll_seconds: int = Field(default=60, ge=15)
    trigger_minutes: int = Field(default=15, ge=1)
    research_cache_hours: int = Field(default=12, ge=1)
    event_debounce_minutes: int = Field(default=15, ge=1)
    price_trigger: Decimal = Field(default=Decimal("0.05"), gt=0)
    preopen_minutes: int = Field(default=45, ge=1)
    model: str = "gpt-6.1-sol"
    reasoning_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    role_models: dict[
        Literal["research_lead", "business", "catalysts", "valuation", "adversarial"], ResearchModel
    ] = Field(default_factory=dict)
    research_max_model_calls: int = Field(default=24, ge=5, le=100)
    research_max_tool_calls: int = Field(default=48, ge=4, le=200)
    research_max_seconds: int = Field(default=600, ge=30, le=1800)
    research_max_input_bytes: int = Field(default=180000, ge=10000, le=500000)
    research_max_output_tokens: int = Field(default=3000, ge=256, le=8000)
    research_max_cost_usd: Decimal = Field(default=Decimal(5), gt=0, le=100)
    research_daily_cost_usd: Decimal = Field(default=Decimal(10), ge=0, le=1000)
    x_enabled: bool = False
    x_daily_calls: int = Field(default=6, ge=0, le=30)
    x_cache_hours: int = Field(default=4, ge=1, le=24)
    flovia_x_enabled: bool = False
    flovia_x_daily_calls: int = Field(default=6, ge=0, le=30)
    flovia_x_cache_hours: int = Field(default=4, ge=1, le=24)
    sec_user_agent: str = ""
    input_cost_per_million: Decimal | None = Field(default=None, ge=0)
    output_cost_per_million: Decimal | None = Field(default=None, ge=0)
    universe: dict[Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9.\-]{0,9}$")], Security] = Field(
        default_factory=dict
    )

    def research_model(self, role: str) -> ResearchModel:
        if role in self.role_models:
            return self.role_models[role]
        if self.input_cost_per_million is None or self.output_cost_per_million is None:
            raise ValueError("Configure model token rates before research to enforce estimated spend limits")
        return ResearchModel(
            model=self.model,
            reasoning_effort=self.reasoning_effort,
            input_cost_per_million=self.input_cost_per_million,
            output_cost_per_million=self.output_cost_per_million,
        )

    @model_validator(mode="after")
    def paper_only(self):
        if self.broker_url != PAPER_URL:
            raise ValueError("Only https://paper-api.alpaca.markets is allowed")
        for name in ("APCA_API_BASE_URL", "ALPACA_BASE_URL", "ALPACA_TRADING_URL"):
            if os.getenv(name, PAPER_URL) != PAPER_URL:
                raise ValueError(f"{name} points outside the paper endpoint")
        return self

    @classmethod
    def load(cls, path: str | Path = "config.toml") -> Settings:
        with Path(path).open("rb") as f:
            return cls.model_validate(tomllib.load(f))


def credentials() -> tuple[str, str]:
    key = os.getenv("ALPACA_PAPER_API_KEY")
    secret = os.getenv("ALPACA_PAPER_SECRET_KEY")
    if not key or not secret:
        raise ValueError(
            "Set ALPACA_PAPER_API_KEY and ALPACA_PAPER_SECRET_KEY from a dedicated paper account"
        )
    return key, secret
