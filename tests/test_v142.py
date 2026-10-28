"""v1.4.2: richer, source-honest editorial templates."""
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from invest_blog.lessons import TERMS
from invest_blog.posts import PostGenerator
from invest_blog.model import Instrument
from test_posts import FakeApi, mv


class SingleBondAPI(FakeApi):
    def portfolio(self, account):
        return {'totalAmountPortfolio': mv(50000), 'positions': [
            {'instrumentType': 'bond', 'instrumentUid': 'bond',
             'quantity': {'units': '5', 'nano': 0},
             # Prices of bonds can be expressed in percent of nominal!
             'currentPrice': {'units': '101', 'nano': 0, 'currency': 'pt.'}}
        ]}

    def bond(self, uid):
        return {'maturityDate': '2028-05-01T00:00:00Z',
                'floatingCouponFlag': False, 'amortizationFlag': True,
                'nominal': {'units': '0', 'nano': 0, 'currency': 'rub'}}


class SingleShareAPI(FakeApi):
    def portfolio(self, account):
        return {'totalAmountPortfolio': mv(50000), 'positions': [
            {'instrumentType': 'share', 'instrumentUid': 'share',
             'quantity': {'units': '3', 'nano': 0},
             'currentPrice': mv(100)}
        ]}


def generator(api):
    return PostGenerator(api, ('account',), Path('/tmp/no_cbr_file_for_tests.json'))


@pytest.mark.parametrize('klass', [SingleBondAPI, SingleShareAPI])
def test_paper_has_agreed_editorial_sections(klass):
    draft = generator(klass()).paper(date(2026, 10, 28))
    required = (
        'Что за компания?', 'Что происходит с компанией сейчас?',
        'Что по самой бумаге?', 'Какие перспективы?',
        'Что мне нравится', 'Основные риски', 'Моё мнение', 'Итог',
        'источник', 'портфеле', 'не индивидуальная инвестиционная рекомендация',
    )
    for heading in required:
        assert heading.casefold() in draft.body.casefold(), heading
    assert 230 <= len(draft.body.split()) <= 390
    assert len(draft.body) < 3900  # fits in bot's inline-text flow
    assert draft.kind == 'paper'
    assert draft.period == '2026-10-26'


def test_bond_does_not_multiply_percent_price_into_rubles():
    post = generator(SingleBondAPI()).paper(date(2026, 10, 28)).body
    assert 'Текущая оценка позиции' not in post
    assert '505,00 ₽' not in post
    assert 'Примерная доля портфеля' not in post
    assert 'Текущий номинал: [сверить условия' in post
    assert 'Амортизация: да' in post
    assert '01.05.2028' in post
    assert 'Кредитный рейтинг: [агентство' in post


def test_stock_has_ruble_price_and_portfolio_share():
    post = generator(SingleShareAPI()).paper(date(2026, 10, 28)).body
    assert 'Текущая цена (снимок API): 100,00 ₽' in post
    assert 'Примерная доля портфеля: 0,60%' in post
    assert 'Дивидендная политика / отсечка:' in post
    assert 'Кредитный рейтинг:' not in post


def test_paper_has_no_fabricated_business_or_forecast():
    post = generator(SingleShareAPI()).paper(date(2026, 10, 28)).body
    assert '[Чем занимается эмитент' in post
    assert '[Последняя доступная отчётность' in post
    assert '[На горизонте 1–3 лет' in post
    assert '[Почему эта бумага в портфеле?' in post
    assert 'доходность 20%' not in post


def test_all_glossary_lessons_are_rich_and_readable():
    assert len(TERMS) == 12
    assert len({title for title, _ in TERMS}) == 12
    for title, body in TERMS:
        assert 155 <= len(body.split()) <= 235, (title, len(body.split()))
        assert len(body) < 3700, title
        for label in ('Что это такое?', 'Как это работает?', 'Разберём на примере',
                      'Почему это важно инвестору?', 'О чём часто забывают?', 'Что запомнить'):
            assert label in body, (title, label)
        assert '[' not in body, title  # fully drafted lessons, not blank templates


def test_glossary_draft_includes_title_and_natural_ending():
    gen = generator(FakeApi())
    draft = gen.glossary(date(2026, 10, 28))
    assert draft.kind == 'glossary'
    assert draft.period == '2026-10-26'
    assert draft.body.startswith('📚 Ликбез за 2 минуты: ')
    assert draft.body.endswith('О каком понятии рассказать в следующий раз? 👇')
    assert gen.selected_glossary_topic in draft.body
    assert len(draft.body) < 3900


def test_glossary_rotates_and_skips_recent_topics():
    gen = generator(FakeApi())
    first = gen.glossary(date(2026, 10, 28))
    selected = gen.selected_glossary_topic
    assert selected
    gen.recent_glossary = (selected,)
    next_draft = gen.glossary(date(2026, 11, 4))
    assert gen.selected_glossary_topic != selected
    assert next_draft.body != first.body
