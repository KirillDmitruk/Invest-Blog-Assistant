from datetime import date, timedelta
from invest_blog.placements import render_draft


def test_radar_draft_has_full_editorial_sections_without_fabrication():
    data = {'name':'Тестовый эмитент 001P', 'isin':'RU0000000000',
            'placement_date':(date.today()+timedelta(days=2)).isoformat(),
            'coupon_type':'Плавающий', 'coupon_frequency':12,'source':'tinvest'}
    body=render_draft(data)
    assert 'На радаре' in body
    assert 'Коротко о компании' in body
    assert 'На что обратить внимание' in body
    assert 'Мой взгляд' in body
    assert 'рейтинг, агентство и дата — проверить' in body
    assert 'поручителей' in body.lower()
    assert '2,6%' not in body


def test_radar_draft_keeps_verified_rating():
    data={'name':'Выпуск', 'placement_date':date.today().isoformat(),
          'credit_rating':'AA-.ru','rating_agency':'НКР',
          'rating_date':'2026-10-30',
          'rating_source_url':'https://example.org/rating'}
    assert 'AA-.ru (НКР, 2026-10-30)' in render_draft(data)
