"""Editorial template v1.5.2 — compact bond placements with verified ratings."""
from datetime import date

from invest_blog.placements import from_tinvest, from_feed, render_draft

DAY = date(2026, 10, 30)

def sample():
    return dict(source='tinvest',name='Тестовые облигации 001Р',
                ticker='TEST',isin='RU000000TEST',placement_date='2026-11-15',
                maturity_date='2028-11-15',nominal=1000,nominal_currency='RUB',
                placement_price=1000,price_currency='RUB',coupon_type='Фиксированный',
                coupon_frequency=4,coupon_amount=None,offer_date=None,source_url=None)

def test_short_template_simple_no_invented_rating():
    text=render_draft(sample())
    assert 'На радаре: Тестовые облигации' in text
    assert 'Кредитный рейтинг: [проверить рейтинг, агентство и дату]' in text
    assert 'Купон за выплату:' not in text
    assert '4 раза в год' in text
    assert len(text)<1200
    assert text.count('🛰')==1
    for emoji in ('💰','🏢','⚠️','🔎','🤔','📅'):
        assert emoji not in text
    assert 'размещение' in text.lower()
    assert 'Оферта:' in text

def test_rating_only_when_full_official_reference():
    data={**sample(), 'credit_rating':'AA-(RU)', 'rating_agency':'АКРА',
          'rating_date':'2026-10-25','rating_source_url':'https://acra-ratings.ru/ratings/issue'}
    text=render_draft(data)
    assert 'AA-(RU) — АКРА (на 2026-10-25)' in text
    assert 'Источник рейтинга: https://acra-ratings.ru/ratings/issue' in text
    data.pop('rating_source_url')
    assert '[проверить рейтинг, агентство и дату]' in render_draft(data)
    data['rating_source_url']='http://unsafe.test/'
    assert '[проверить рейтинг, агентство и дату]' in render_draft(data)

def test_optional_feed_rating_fields():
    feed=from_feed({'id':'test1','name':'Бумага', 'placement_date':'2026-11-15',
                    'credit_rating':'AA-(RU)', 'rating_agency':'АКРА',
                    'rating_date':'2026-10-25',
                    'rating_source_url':'https://acra-ratings.ru/doc'}, DAY)
    assert feed['credit_rating']=='AA-(RU)'
    assert 'AA-(RU) — АКРА' in render_draft(feed)
    feed_bad=from_feed({'id':'test1','name':'Бумага','placement_date':'2026-11-15',
                        'credit_rating':'AAA', 'rating_agency':'АКРА',
                        'rating_date':'2026-10-25',
                        'rating_source_url':'http://evil.test'}, DAY)
    assert feed_bad['rating_source_url'] is None
    assert 'AAA — АКРА' not in render_draft(feed_bad)

def test_coupon_value_label_and_optional():
    data={**sample(),'coupon_amount':35.5,'coupon_currency':'RUB'}
    text=render_draft(data)
    assert 'Купон за выплату: 35.5 RUB' in text
    assert len(text)<1200
