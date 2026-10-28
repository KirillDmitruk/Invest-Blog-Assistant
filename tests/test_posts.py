from datetime import date
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from invest_blog.model import fmt_totals, money, quotation, utc_bounds
from invest_blog.posts import PostGenerator, current_week, previous_month, previous_workweek
from invest_blog.storage import DraftStore
from invest_blog.model import Draft
from invest_blog.tinvest import TInvest, TInvestError


def mv(value: int, currency='RUB'):
    return {'units': str(value), 'nano': 0, 'currency': currency}


def op(identifier: str, typ: str, amount: int, day: str, currency='RUB', **kwargs):
    return {'id': identifier, 'date': f'{day}T15:00:00Z', 'operationType': typ,
            'payment': mv(amount, currency), 'state': 'OPERATION_STATE_EXECUTED', **kwargs}


class FakeApi:
    tz = ZoneInfo('Europe/Moscow')

    def __init__(self):
        self.operations_seen = []
        self.data = [
            op('1', 'OPERATION_TYPE_COUPON', 400, '2026-10-26', instrumentUid='bond'),
            op('2', 'OPERATION_TYPE_DIVIDEND', 100, '2026-10-27', instrumentUid='share'),
            op('3', 'OPERATION_TYPE_INPUT', 1000, '2026-10-27'),
            op('4', 'OPERATION_TYPE_BOND_REPAYMENT', 2000, '2026-10-28', instrumentUid='bond'),
            op('5', 'OPERATION_TYPE_COUPON', 10, '2026-10-28', currency='USD', instrumentUid='bond'),
            op('6', 'OPERATION_TYPE_COUPON', 900, '2026-10-31', instrumentUid='bond'),
        ]

    def operations(self, account, start, end):
        self.operations_seen.append((account, start, end))
        return [o for o in self.data if start <= date.fromisoformat(o['date'][:10]) <= end]

    def portfolio(self, account):
        return {'totalAmountPortfolio': mv(50000), 'positions': [
            {'instrumentType': 'bond', 'instrumentUid': 'bond', 'quantity': mv(5), 'currentPrice': mv(1000)},
            {'instrumentType': 'share', 'instrumentUid': 'share', 'quantity': mv(3), 'currentPrice': mv(100)},
        ]}

    def instrument(self, uid='', figi=''):
        from invest_blog.model import Instrument
        key = uid or figi
        return Instrument(uid=key, figi='x', name='Тестовая облигация' if key == 'bond' else 'Тестовая акция',
                          isin='RU000TEST', ticker='TEST', kind='bond' if key == 'bond' else 'share',
                          currency='RUB', raw={})

    def coupons(self, uid, start, end):
        return [{'couponDate': '2026-10-29T08:00:00Z', 'payOneBond': mv(30)}]

    def dividends(self, uid, start, end):
        return [{'recordDate': '2026-10-27T00:00:00Z', 'paymentDate': '2026-10-30T08:00:00Z',
                 'lastBuyDate': '2026-10-26T08:00:00Z', 'dividendNet': mv(8), 'dividendType': 'Regular Cash'}]

    def bond(self, uid):
        return {'maturityDate': '2028-05-01T00:00:00Z', 'floatingCouponFlag': False, 'amortizationFlag': True}


def make(tmp_path):
    api = FakeApi()
    p = tmp_path / 'cbr.json'
    p.write_text('[{"date":"2026-10-30","title":"Заседание ЦБ","source":"официально"}]', encoding='utf-8')
    return PostGenerator(api, ('account',), p), api


def test_periods():
    assert previous_workweek(date(2026, 10, 31)) == (date(2026, 10, 26), date(2026, 10, 30))
    assert previous_workweek(date(2026, 10, 28)) == (date(2026, 10, 19), date(2026, 10, 23))
    assert previous_month(date(2026, 1, 1)) == (date(2025, 12, 1), date(2025, 12, 31))
    assert current_week(date(2026, 10, 28)) == (date(2026, 10, 26), date(2026, 11, 1))


def test_money_and_timezone():
    assert quotation({'units':'-2', 'nano':-500000000}) == Decimal('-2.5')
    assert money({'units':'10', 'nano':500000000, 'currency':'usd'}) == (Decimal('10.5'), 'USD')
    a,b = utc_bounds(date(2026, 10, 26), date(2026, 10, 30), ZoneInfo('Europe/Moscow'))
    assert a == '2026-10-25T21:00:00Z'
    assert b == '2026-10-30T21:00:00Z'


def test_weekly_no_weekend_or_purchases(tmp_path):
    gen, api = make(tmp_path)
    draft = gen.weekly(date(2026,10,31))
    assert 'Тестовая облигация $RU000TEST' in draft.body
    assert 'Купоны — 400,00 ₽, $10,00' in draft.body  # separate currencies
    assert 'Пополнения — 1 000,00 ₽' in draft.body
    assert 'Погашения — 2 000,00 ₽' in draft.body
    assert '900' not in draft.body
    assert api.operations_seen[0][1:] == (date(2026,10,26), date(2026,10,30))


def test_monthly_previous_month_and_snapshot(tmp_path):
    gen, api = make(tmp_path)
    draft = gen.monthly(date(2026,11,1))
    assert draft.period == '2026-10'
    assert 'Стоимость портфеля на сегодня: 50 000,00 ₽' in draft.body
    assert 'текущий снимок' in draft.body


def test_plan_dates_and_cbr(tmp_path):
    gen, api = make(tmp_path)
    draft = gen.plan(date(2026,10,26))
    assert '29.10.2026' in draft.body
    assert '~150,00 ₽' in draft.body
    assert '30.10.2026 — Тестовая акция' in draft.body
    assert '~24,00 ₽' in draft.body
    assert '26.10.2026 — Тестовая акция' in draft.body
    assert 'Заседание ЦБ' in draft.body
    assert '[добавить вручную]' in draft.body


def test_paper_no_fake_rating(tmp_path):
    gen, api = make(tmp_path)
    draft = gen.paper(date(2026,10,28))
    assert 'Что мне нравится' in draft.body
    assert 'Основные риски' in draft.body
    assert 'проверить' in draft.body or 'Дивиденды' in draft.body


def test_no_events_file_is_explicit(tmp_path):
    gen, api = make(tmp_path)
    gen.events_path = tmp_path / 'missing.json'
    assert 'Список событий ЦБ не подключён' in gen.plan(date(2026,10,26)).body


def test_dedup_and_edit(tmp_path):
    store = DraftStore(tmp_path/'new.sqlite3')
    post = Draft('weekly', 'a_b', 'old')
    one, fresh = store.insert(post, scheduled=True)
    two, second = store.insert(post, scheduled=True)
    assert one == two and fresh and not second
    third, third_fresh = store.insert(post, scheduled=False)
    assert third != one and third_fresh
    store.set_body(one, 'updated')
    assert store.get(one)['body'] == 'updated'
    store.link_message(1, one)
    assert store.draft_for_message(1) == one
    store.mark_done(one)
    assert store.get(one)['state'] == 'published'


class _Response:
    def __init__(self, payload):
        self.status_code=200
        self.ok=True
        self.payload=payload
        self.text=''
    def json(self): return self.payload


class _Session:
    def __init__(self, pages):
        self.headers = {}
        self.pages = iter(pages)
        self.requests = []
    def post(self, url, json, timeout):
        self.requests.append(dict(json))
        return _Response(next(self.pages))


def test_cursor_pagination_and_local_dates():
    # 21:20 UTC = 00:20 Moscow on Oct 26; 20:30 UTC = 23:30 Moscow Oct 25.
    first = op('a', 'OPERATION_TYPE_INPUT', 100, '2026-10-25')
    first['date'] = '2026-10-25T21:20:00Z'
    outside = op('b', 'OPERATION_TYPE_INPUT', 100, '2026-10-25')
    outside['date'] = '2026-10-25T20:30:00Z'
    other = op('c', 'OPERATION_TYPE_INPUT', 100, '2026-10-26')
    s = _Session([{'items':[first, outside], 'hasNext':True, 'nextCursor':'page2'},
                  {'items':[first, other], 'hasNext':False}])
    api = TInvest('secret', ZoneInfo('Europe/Moscow'), session=s)
    items = api.operations('A', date(2026,10,26), date(2026,10,26))
    assert [x['id'] for x in items] == ['c', 'a']
    assert s.requests[1]['cursor'] == 'page2'
    assert s.requests[0]['from'] == '2026-10-25T21:00:00Z'


def test_purchase_fallback_template_if_no_buys(tmp_path):
    gen, api = make(tmp_path)
    draft = gen.purchase(date(2026, 10, 28))
    assert 'Количество: [шт.]' in draft.body
    assert 'За последние 60 дней покупок в API не найдено' in draft.body


def test_purchase_executed_operation(tmp_path):
    gen, api = make(tmp_path)
    api.data.append(op('buy', 'OPERATION_TYPE_BUY', -3120, '2026-10-28',
                       instrumentUid='bond', quantity='3', quantityDone='0', price=mv(1040)))
    draft = gen.purchase(date(2026, 10, 28))
    assert '3 шт. по 1 040,00 ₽' in draft.body
    assert '3 120,00 ₽' in draft.body
    assert 'Кредитный рейтинг: [внешний источник]' in draft.body


def test_plan_record_date(tmp_path):
    gen, api = make(tmp_path)
    assert '27.10.2026 — Тестовая акция $TEST: дата закрытия реестра' in gen.plan(date(2026,10,26)).body


def test_get_operations_by_cursor_real_type_and_monthly_summary(tmp_path):
    """Real REST OperationItem uses `type`, not `operationType`."""
    data = [
        {'id': 'coupon1', 'date': '2026-09-03T09:00:00Z', 'type': 'OPERATION_TYPE_COUPON',
         'state': 'OPERATION_STATE_EXECUTED', 'payment': mv(250), 'name': 'Купон'},
        {'id': 'input1', 'date': '2026-09-04T09:00:00Z', 'type': 'OPERATION_TYPE_INPUT',
         'state': 'OPERATION_STATE_EXECUTED', 'payment': mv(3000)},
        {'id': 'buy1', 'date': '2026-09-10T09:00:00Z', 'type': 'OPERATION_TYPE_BUY',
         'state': 'OPERATION_STATE_EXECUTED', 'payment': mv(-1020), 'quantity': '1'},
        {'id': 'div1', 'date': '2026-09-15T09:00:00Z', 'type': 'OPERATION_TYPE_DIVIDEND',
         'state': 'OPERATION_STATE_EXECUTED', 'payment': mv(120), 'name': 'Дивиденды'},
        {'id': 'fee1', 'date': '2026-09-15T09:00:00Z', 'type': 'OPERATION_TYPE_BROKER_FEE',
         'state': 'OPERATION_STATE_EXECUTED', 'payment': mv(-3)},
    ]
    session = _Session([{'items': data, 'hasNext': False}])
    api = TInvest('secret', ZoneInfo('Europe/Moscow'), session=session)
    api.portfolio = lambda account: {'totalAmountPortfolio': mv(25000), 'positions': []}
    api.instrument = lambda uid='', figi='': (_ for _ in ()).throw(TInvestError('not found'))
    gen = PostGenerator(api, ('account1',), tmp_path / 'events.json')
    draft = gen.monthly(date(2026, 10, 28))
    assert '250,00 ₽' in draft.body
    assert 'Купоны — 250,00 ₽' in draft.body
    assert 'Дивиденды — 120,00 ₽' in draft.body
    assert 'Пополнения — 3 000,00 ₽' in draft.body
    assert '🛒 Покупки за месяц' in draft.body
    assert 'Количество операций — 1' in draft.body
    assert 'Сумма списаний — 1 020,00 ₽' in draft.body
    assert 'Комиссии' not in draft.body


def test_type_from_api_purchase_post(tmp_path):
    gen, api = make(tmp_path)
    api.data = [
        {'id': 'purchase', 'date': '2026-10-28T09:00:00Z', 'type': 'OPERATION_TYPE_BUY',
         'state': 'OPERATION_STATE_EXECUTED', 'payment': mv(-2000),
         'instrumentUid': 'bond', 'quantity': '2', 'price': mv(1000)}]
    draft = gen.purchase(date(2026, 10, 28))
    assert '2 шт. по 1 000,00 ₽' in draft.body
    assert '2 000,00 ₽' in draft.body


def test_empty_cursor_items_are_valid():
    s = _Session([{'hasNext': False}])  # REST may omit an empty repeated field.
    api = TInvest('secret', ZoneInfo('Europe/Moscow'), session=s)
    assert api.operations('account', date(2026,9,1), date(2026,9,30)) == []


def test_invalid_cursor_item_fails_loudly():
    import pytest
    s = _Session([{'hasNext': False, 'items': [{'id': '1', 'date': '2026-09-10T09:00:00Z',
                                               'payment': mv(120)}]}])
    api = TInvest('secret', ZoneInfo('Europe/Moscow'), session=s)
    with pytest.raises(TInvestError, match='нет поля type'):
        api.operations('account', date(2026,9,1), date(2026,9,30))
