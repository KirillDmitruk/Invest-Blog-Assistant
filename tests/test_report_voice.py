"""Livelier reports retain ledger semantics and do not invent goal achievements."""
from datetime import date
from decimal import Decimal
from test_posts import make,op,mv

def test_weekly_monthly_goal_uses_month_to_date_cash_not_weekly_annualization(tmp_path):
    gen,api=make(tmp_path)
    api.data=[op('a','OPERATION_TYPE_COUPON',600,'2026-11-02',instrumentUid='bond'),
        op('b','OPERATION_TYPE_COUPON',100,'2026-11-19',instrumentUid='bond'),
        op('c','OPERATION_TYPE_DIVIDEND',50,'2026-11-19',instrumentUid='share'),
        op('d','OPERATION_TYPE_INPUT',900,'2026-11-19'),
        op('e','OPERATION_TYPE_COUPON',1000,'2026-11-19',currency='USD',instrumentUid='bond')]
    text=gen.weekly(date(2026,11,21)).body
    assert 'Всего купонов: 100,00 ₽, $1 000,00' in text
    assert 'Итого поступлений: 1 050,00 ₽, $1 000,00' in text
    assert 'по 20.11: 750,00 ₽' in text
    assert '✅ 750 ₽ за месяц' in text and '🎯 1 000 ₽ за месяц' in text
    assert '🎯 100 000 ₽' in text and '✅ 100 000 ₽' not in text

def test_monthly_merges_same_issue_but_keeps_currencies_and_repayment_distinct(tmp_path):
    gen,api=make(tmp_path)
    api.data=[op('a','OPERATION_TYPE_COUPON',100,'2026-10-02',instrumentUid='bond'),
        op('b','OPERATION_TYPE_COUPON',200,'2026-10-19',instrumentUid='bond'),
        op('c','OPERATION_TYPE_COUPON',5,'2026-10-19',currency='USD',instrumentUid='bond'),
        op('d','OPERATION_TYPE_BOND_REPAYMENT',1000,'2026-10-19',instrumentUid='bond')]
    text=gen.monthly(date(2026,11,1)).body
    assert 'Тестовая облигация $RU000TEST — 300,00 ₽' in text
    assert 'Итого поступлений: 1 300,00 ₽, $5,00' in text
    assert 'по 31.10: 300,00 ₽' in text and '🎯 500 ₽ за месяц' in text
    assert 'возврат вложенного номинала' in text and '3 купонные выплаты' in text

def test_empty_period_does_not_claim_growth_or_received_coupons(tmp_path):
    gen,api=make(tmp_path);api.data=[]
    text=gen.weekly(date(2026,11,21)).body
    assert 'Купоны капают' not in text and 'капитал растёт' not in text
    assert 'Итого поступлений: 0 ₽' in text and '✅ 500 ₽ за месяц' not in text
    assert 'купонных выплат не было' in text
