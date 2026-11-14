"""On-demand, read-only T-Invest activity report for an investment blog.

No token, account ID, or raw broker response is included in the output.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal
from zoneinfo import ZoneInfo

from .model import fmt_money, fmt_num, iso_day, money
from .posts import BUY, COUPON, DEPOSIT, DIVIDEND, REPAYMENT, operation_type

SELL = {"OPERATION_TYPE_SELL", "OPERATION_TYPE_SELL_CARD", "OPERATION_TYPE_SELL_MARGIN"}
FEES = {
    "OPERATION_TYPE_BROKER_FEE", "OPERATION_TYPE_SERVICE_FEE",
    "OPERATION_TYPE_MARGIN_FEE", "OPERATION_TYPE_SUCCESS_FEE",
    "OPERATION_TYPE_TRACK_MFEE", "OPERATION_TYPE_TRACK_PFEE",
    "OPERATION_TYPE_CASH_FEE", "OPERATION_TYPE_OUT_FEE",
}
TAXES = {
    "OPERATION_TYPE_TAX", "OPERATION_TYPE_TAX_CORRECTION",
    "OPERATION_TYPE_TAX_BACK", "OPERATION_TYPE_TAX_PROGRESSIVE",
    "OPERATION_TYPE_TAX_REPO", "OPERATION_TYPE_TAX_REPO_HOLD",
    "OPERATION_TYPE_TAX_REPO_REFUND",
}
WITHDRAWALS = {"OPERATION_TYPE_OUTPUT", "OPERATION_TYPE_OUTPUT_SWIFT", "OPERATION_TYPE_OUT_MULTI"}

GROUPS = {
    "coupons": COUPON, "dividends": DIVIDEND, "repayments": REPAYMENT,
    "deposits": DEPOSIT, "buys": BUY, "sells": SELL,
    "fees": FEES, "taxes": TAXES, "withdrawals": WITHDRAWALS,
}
HEADINGS = {
    "coupons": "💰 Купоны", "dividends": "🏦 Дивиденды",
    "repayments": "💸 Погашения облигаций (возврат номинала)",
    "deposits": "📥 Пополнения", "buys": "🛒 Покупки",
    "sells": "📤 Продажи", "fees": "💳 Комиссии",
    "taxes": "🧾 Налоги", "withdrawals": "📤 Выводы средств",
}
OUTFLOWS = {"buys", "fees", "taxes", "withdrawals"}


def _totals(rows: list[dict]) -> dict[str, Decimal]:
    result: dict[str, Decimal] = defaultdict(Decimal)
    for row in rows:
        result[row["currency"]] += Decimal(row["amount"])
    return dict(result)


def _display(values: dict[str, Decimal]) -> str:
    return ", ".join(fmt_money(amount, currency) for currency, amount in sorted(values.items()) if amount) or "0 ₽"


def _quantity(value) -> str:
    if value is None or isinstance(value, bool):
        return ""
    try:
        number = Decimal(str(value))
        return f" · {fmt_num(number, 0)} шт." if number > 0 and number == number.to_integral_value() else (
            f" · {fmt_num(number)} шт." if number > 0 else "")
    except (ValueError, TypeError, ArithmeticError):
        return ""


def build_blog_report(operations: list[dict], start: date, end: date, tz: ZoneInfo,
                      describe=None) -> dict:
    """Pure grouping/formatting; instrument lookups are optional and best-effort."""
    if start > end:
        raise ValueError("Начало периода позже окончания")
    categories: dict[str, list[dict]] = {key: [] for key in GROUPS}
    unknown = 0
    cache: dict[str, str] = {}
    for op in operations:
        kind = operation_type(op)
        group = next((key for key, kinds in GROUPS.items() if kind in kinds), None)
        if not group:
            unknown += 1
            continue
        day = iso_day(op.get("date"), tz)
        if day is None or not start <= day <= end:
            continue
        amount, currency = money(op.get("payment"))
        # Broker cash movements: buys/fees/taxes are generally negative.
        # Display their debit magnitude; do not mix currencies or treat
        # repayments of principal as investment income.
        displayed = abs(amount) if group in OUTFLOWS or group == "sells" else amount
        identity = str(op.get("instrumentUid") or op.get("figi") or "")
        name = str(op.get("name") or "").strip()
        if group in ("coupons", "dividends", "repayments", "buys", "sells"):
            if identity not in cache:
                resolved = None
                if describe and identity:
                    try:
                        resolved = describe(op)
                    except Exception:
                        pass
                cache[identity] = str(resolved or name or op.get("description") or identity or "Бумага")[:180]
            name = cache[identity]
        else:
            name = ""
        categories[group].append({
            "day": day.isoformat(), "name": name, "currency": currency,
            "amount": str(displayed),
            "quantity": str(op.get("quantity")) if op.get("quantity") is not None else None,
        })

    totals = {key: _totals(rows) for key, rows in categories.items()}
    payout_totals: dict[str, Decimal] = defaultdict(Decimal)
    for group in ("coupons", "dividends"):
        for currency, amount in totals[group].items():
            payout_totals[currency] += amount

    lines = [f"📈 Инвестиционные итоги: {start:%d.%m.%Y}–{end:%d.%m.%Y}", ""]
    for key in ("coupons", "dividends", "repayments", "deposits", "buys", "sells", "withdrawals", "fees", "taxes"):
        rows = categories[key]
        if key in ("repayments", "sells", "withdrawals", "fees", "taxes") and not rows:
            continue
        lines.extend([HEADINGS[key]])
        if not rows:
            lines.append("Не было.")
        else:
            for item in sorted(rows, key=lambda r: (r["day"], r["name"])):
                qty = _quantity(item["quantity"]) if key in ("buys", "sells") else ""
                name = f" · {item['name']}" if item["name"] else ""
                lines.append(f"• {date.fromisoformat(item['day']):%d.%m}{name}{qty} — {fmt_money(Decimal(item['amount']), item['currency'])}")
            lines.append(f"Итого: {_display(totals[key])}")
        lines.append("")

    lines.extend([
        "📊 Итоги периода",
        f"• Купоны: {_display(totals['coupons'])}",
        f"• Дивиденды: {_display(totals['dividends'])}",
        f"• Всего купонов и дивидендов: {_display(dict(payout_totals))}",
        f"• Пополнения: {_display(totals['deposits'])}",
        f"• Покупки: {_display(totals['buys'])}",
    ])
    for key, label in (("sells", "Продажи"), ("repayments", "Погашения"), ("withdrawals", "Выводы"),
                       ("fees", "Комиссии"), ("taxes", "Налоги")):
        if categories[key]:
            lines.append(f"• {label}: {_display(totals[key])}")
    lines.extend([
        "", "Все суммы — по исполненным операциям T-Invest за выбранный период.",
        "Погашения — возврат номинала, не купонный доход. Комиссии и налоги показаны отдельно.",
        "Суммы в разных валютах не складываются. Покупки не обязательно оплачены именно полученными выплатами.",
    ])
    return {
        "start": start.isoformat(), "end": end.isoformat(),
        "counts": {key: len(rows) for key, rows in categories.items()},
        "totals": {key: {currency: str(value) for currency, value in values.items()}
                   for key, values in totals.items()},
        "payout_totals": {currency: str(value) for currency, value in payout_totals.items()},
        "entries": categories, "other_operations": unknown,
        "text": "\n".join(lines),
    }
