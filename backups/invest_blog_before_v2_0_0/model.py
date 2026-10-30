from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

NANO = Decimal("0.000000001")


def quotation(value: dict | None) -> Decimal:
    if not value:
        return Decimal(0)
    return Decimal(str(value.get("units", "0"))) + Decimal(str(value.get("nano", 0))) * NANO


def money(value: dict | None) -> tuple[Decimal, str]:
    if not value:
        return Decimal(0), "RUB"
    return quotation(value), (value.get("currency") or "RUB").upper()


def iso_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def iso_day(value: str | None, tz: ZoneInfo) -> date | None:
    return iso_dt(value).astimezone(tz).date() if value else None


def utc_bounds(first: date, last: date, tz: ZoneInfo) -> tuple[str, str]:
    """API boundaries are [first 00:00 local, (last+1) 00:00 local) in UTC."""
    from datetime import timedelta
    a = datetime.combine(first, time.min, tzinfo=tz).astimezone(timezone.utc)
    b = datetime.combine(last + timedelta(days=1), time.min, tzinfo=tz).astimezone(timezone.utc)
    return a.isoformat().replace("+00:00", "Z"), b.isoformat().replace("+00:00", "Z")


def fmt_num(value: Decimal, digits: int = 2) -> str:
    v = value.quantize(Decimal("1").scaleb(-digits))
    return f"{v:,.{digits}f}".replace(",", " ").replace(".", ",")


def fmt_money(value: Decimal, currency: str) -> str:
    symbol = {"RUB": "₽", "USD": "$", "EUR": "€", "CNY": "¥"}.get(currency, currency)
    return f"{fmt_num(value)} {symbol}" if symbol not in ("$", "€", "¥") else f"{symbol}{fmt_num(value)}"


def fmt_totals(grouped: dict[str, Decimal]) -> str:
    parts = [fmt_money(v, c) for c, v in sorted(grouped.items()) if v]
    return ", ".join(parts) if parts else "0 ₽"


def group_amounts(items: list[dict]) -> dict[str, Decimal]:
    sums: dict[str, Decimal] = defaultdict(Decimal)
    for item in items:
        amount, currency = money(item.get("payment"))
        sums[currency] += amount
    return dict(sums)


@dataclass(frozen=True)
class Draft:
    kind: str
    period: str
    body: str


@dataclass(frozen=True)
class Instrument:
    uid: str
    figi: str
    name: str
    isin: str
    ticker: str
    kind: str
    currency: str
    raw: dict[str, Any]

    @property
    def tag(self) -> str:
        code = self.isin if self.kind == "bond" else self.ticker or self.isin
        return f" ${code}" if code else ""
