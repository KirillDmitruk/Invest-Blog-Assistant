"""Activity report uses executed T-Invest cash movements without inventing returns."""
from datetime import date
from zoneinfo import ZoneInfo

from invest_blog.blog_report import build_blog_report


def op(kind, amount, day="2026-11-05T10:00:00Z", uid=None, currency="RUB", quantity=None):
    units = int(amount)
    nano = int(round((amount - units) * 1_000_000_000))
    return {
        "type": kind, "date": day, "instrumentUid": uid,
        "payment": {"units": str(units), "nano": nano, "currency": currency},
        "quantity": quantity,
    }


def test_two_week_report_groups_real_payments_without_double_counting():
    operations = [
        op("OPERATION_TYPE_COUPON", 123.45, uid="bond"),
        op("OPERATION_TYPE_DIVIDEND", 10.0, uid="share"),
        op("OPERATION_TYPE_INPUT", 10000),
        op("OPERATION_TYPE_BUY", -3456.78, uid="bond", quantity=3),
        op("OPERATION_TYPE_SELL", 1000, uid="share", quantity=1),
        op("OPERATION_TYPE_BOND_REPAYMENT_FULL", 1000, uid="bond"),
        op("OPERATION_TYPE_BROKER_FEE", -1.5),
        op("OPERATION_TYPE_TAX", -5),
        op("OPERATION_TYPE_TAX_BACK", 2),
        op("OPERATION_TYPE_COUPON", 500, day="2026-11-14T12:00:00Z"),
    ]
    seen = []
    def describe(item):
        seen.append(item["instrumentUid"])
        return {"bond": "Облигация А", "share": "Акция Б"}[item["instrumentUid"]]

    report = build_blog_report(operations, date(2026, 11, 2), date(2026, 11, 13),
                               ZoneInfo("Europe/Moscow"), describe)
    assert report["totals"]["coupons"]["RUB"] == "123.45"
    assert report["totals"]["dividends"]["RUB"] == "10.0"
    assert report["payout_totals"]["RUB"] == "133.45"
    assert report["totals"]["deposits"]["RUB"] == "10000"
    assert report["totals"]["buys"]["RUB"] == "3456.78"
    assert report["totals"]["repayments"]["RUB"] == "1000"
    assert report["totals"]["taxes"]["RUB"] == "3"
    assert report["counts"]["coupons"] == 1
    assert "3 шт." in report["text"]
    assert "Облигация А" in report["text"]
    assert "возврат номинала" in report["text"]
    assert len(seen) == 2  # One lookup per instrument, not per operation.


def test_currencies_stay_separate_and_missing_lookup_does_not_fail():
    operations = [
        op("OPERATION_TYPE_COUPON", 100, uid="bond"),
        op("OPERATION_TYPE_COUPON", 5, uid="usd", currency="USD"),
    ]
    def broken(_):
        raise RuntimeError("Instrument endpoint temporarily unavailable")

    report = build_blog_report(operations, date(2026, 11, 2), date(2026, 11, 13),
                               ZoneInfo("Europe/Moscow"), broken)
    assert report["payout_totals"] == {"RUB": "100", "USD": "5"}
    assert "100,00 ₽" in report["text"]
    assert "$5,00" in report["text"]


def test_local_timezone_decides_inclusion():
    operations = [op("OPERATION_TYPE_COUPON", 30, day="2026-11-01T22:30:00Z")]
    report = build_blog_report(operations, date(2026, 11, 2), date(2026, 11, 13),
                               ZoneInfo("Europe/Moscow"))
    assert report["counts"]["coupons"] == 1
