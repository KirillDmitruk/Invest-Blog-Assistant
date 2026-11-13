"""No live broker or Telegram calls. Isolated accounts, risk evidence and durable scheduling."""
import gc
import json
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from invest_blog.config import Settings
from invest_blog.iis_review import ReviewStore, analyze_iis, rating_bucket, ratings_for, run_review
from invest_blog.model import Instrument
from invest_blog.placements import PlacementStore, from_tinvest
from invest_blog.portfolio_store import PortfolioStore

TODAY = date(2026, 11, 13)
NOW = datetime(2026, 11, 13, 9, tzinfo=timezone.utc)


def mv(n, currency='RUB'):
    return {'units': str(n), 'nano': 0, 'currency': currency}


def meta(issuer='issuer-a', rating='AA(RU)', **kwargs):
    return dict({'nominal': mv(1000), 'issuerUid': issuer, 'issuerName': issuer,
                 'isin': 'RU000TEST', 'sector': 'finance', 'floatingCouponFlag': False,
                 'amortizationFlag': False, 'subordinatedFlag': False, 'perpetualFlag': False,
                 'maturityDate': '2028-01-01', 'forIisFlag': True,
                 'ratings': [{'agencyName': 'АКРА', 'ratingLevel': rating, 'ratingDate': '2026-11-01'}]}, **kwargs)


@pytest.fixture
def store(tmp_path):
    store = PortfolioStore(tmp_path/'test.db')
    with store.connect() as db:
        for aid, day in [('iis', '2026-11-13'), ('other', '2026-11-14')]:
            db.execute('INSERT INTO portfolio_snapshots VALUES(?,?,?,?,?,?,?,?,?)', (day, aid, '12000', '10000', '2000', '0', '0', '0', day+'T09:00:00Z'))
        for aid, uid, qty, day in [('iis', 'a', '3', '2026-11-13'), ('iis', 'b', '7', '2026-11-13'), ('other', 'foreign', '100', '2026-11-14')]:
            db.execute('INSERT INTO position_snapshots VALUES(?,?,?,?,?,?,?,?,?,?,?)', (day, aid, uid, uid, uid, 'bond', qty, '990', 'RUB', '10', '2028-01-01'))
            db.execute('INSERT INTO bond_metadata VALUES(?,?,?)', (uid, json.dumps(meta('same' if uid in ('a', 'b') else uid)), '2026-11-13'))
    yield store
    gc.collect()


def analyze(store, **kwargs):
    return analyze_iis(store, 'iis', today=TODAY, **kwargs)


def test_account_isolation_latest_day_and_issuer_aggregation(store):
    r = analyze(store)
    assert r['as_of'] == '2026-11-13'
    assert r['summary']['bond_count'] == 2
    assert r['summary']['known_principal_rub'] == 10000
    assert len(r['issuers']) == 1
    assert r['issuers'][0]['pct'] == 100
    assert r['bonds'][0]['action'] == 'review'
    assert r['concentration_actions'][0]['excess_principal_rub'] == 8500
    assert r['bonds'][0]['market_value_rub'] == 7000


def test_absent_rating_issuer_not_low_and_not_inferred_from_name(store):
    with store.connect() as db:
        db.execute('UPDATE bond_metadata SET raw_json=?', (json.dumps({'nominal': mv(1000)}),))
    r = analyze(store)
    assert all(b['risk'] == 'unknown' for b in r['bonds'])
    assert not r['issuers'] and not r['summary']['issuer_complete']
    assert all(b['action'] == 'verify' for b in r['bonds'])


def test_broker_risk_is_labelled_and_cannot_replace_rating(store):
    with store.connect() as db:
        db.execute('UPDATE bond_metadata SET raw_json=?', (json.dumps(meta(ratings=[], riskLevel='RISK_LEVEL_LOW')),))
    r = analyze(store)
    assert all(b['risk_source'] == 'Уровень брокера' for b in r['bonds'])
    assert all(not b['has_rating'] and b['gaps'] for b in r['bonds'])


@pytest.mark.parametrize('level,bucket', [('ruAAA', 'low'), ('AA-(RU)', 'low'), ('ruA+', 'moderate'), ('BBB(RU)', 'moderate'), ('ruBB+', 'high'), ('D(RU)', 'high'), ('AAA', None), ('WR(RU)', None)])
def test_national_scales_only(level, bucket):
    assert rating_bucket(level) == bucket


def test_latest_per_agency_worst_across_agencies_and_stale():
    raw = {'ratings': [{'agencyName': 'A', 'ratingLevel': 'B(RU)', 'ratingDate': '2025-12-01'},
                       {'agencyName': 'A', 'ratingLevel': 'AAA(RU)', 'ratingDate': '2026-10-01'},
                       {'agencyName': 'B', 'ratingLevel': 'BBB(RU)', 'ratingDate': '2026-10-01'}]}
    assert ratings_for(raw, {}, TODAY, 365)[1] == 'moderate'
    assert ratings_for(raw, {}, TODAY, 10)[1] is None
    raw['ratings'].append({'agencyName': 'A', 'ratingLevel': 'ruBB', 'ratingDate': '2027-01-01'})
    assert ratings_for(raw, {}, TODAY, 365)[1] == 'moderate'


def test_subordination_overrides_high_rating_and_float_not_safe(store):
    with store.connect() as db:
        db.execute('UPDATE bond_metadata SET raw_json=? WHERE instrument_id=?', (json.dumps(meta(subordinatedFlag=True, floatingCouponFlag=True)), 'a'))
    row = next(b for b in analyze(store)['bonds'] if b['id'] == 'a')
    assert row['risk'] == 'high' and 'Субординированный долг' in row['reasons']


def test_foreign_nominal_and_missing_nkd_never_fabricate_rub(store):
    with store.connect() as db:
        db.execute('UPDATE bond_metadata SET raw_json=? WHERE instrument_id=?', (json.dumps(meta(nominal=mv(1000, 'USD'))), 'a'))
        db.execute('UPDATE position_snapshots SET nkd=NULL WHERE instrument_id=?', ('a',))
    r = analyze(store)
    row = next(b for b in r['bonds'] if b['id'] == 'a')
    assert row['principal_rub'] is None and row['market_value_rub'] is None
    assert not r['summary']['principal_complete']


def test_manual_evidence_persists_and_needs_source(store):
    reviews = ReviewStore(store.path)
    with pytest.raises(ValueError):
        reviews.save_evidence('a', {'issuer_id': '123', 'issuer_name': 'Issuer'})
    value = reviews.save_evidence('a', {'issuer_id': '123', 'issuer_name': 'Issuer', 'source_url': 'https://issuer.example/report', 'rating': 'ruBB', 'rating_agency': 'Эксперт РА', 'rating_date': '2026-11-01'})
    assert reviews.evidence()['a'] == value
    row = next(b for b in analyze(store, evidence=reviews.evidence())['bonds'] if b['id'] == 'a')
    assert row['issuer_id'] == '123' and row['risk'] == 'high'
    with pytest.raises(ValueError):
        reviews.save_policy({'issuer_limit_pct': float('nan'), 'high_risk_limit_pct': 20, 'rating_max_days': 365})


def test_new_issue_same_issuer_not_diversification_and_no_iis_no_candidate(store):
    item = {'id': 1, 'name': 'New', 'isin': 'NEW', 'uid': 'new', 'issuer_uid': 'same', 'for_iis': True,
            'ratings': meta()['ratings'], 'subordinated': False, 'perpetual': False, 'placement_date': '2026-12-01'}
    r = analyze(store, placements=[item])
    assert r['candidates'][0]['status'] == 'verify'
    assert any('концентрацию' in reason for reason in r['candidates'][0]['reasons'])
    item['issuer_uid'] = 'new-issuer'
    assert analyze(store, placements=[item])['candidates'][0]['status'] == 'research'
    item['for_iis'] = None
    assert analyze(store, placements=[item])['candidates'][0]['status'] == 'verify'


def test_changes_and_history_are_account_scoped(store):
    reviews = ReviewStore(store.path)
    before = analyze(store)
    reviews.append('iis', before, NOW)
    reviews.append('other', dict(before, summary={'bond_count': 999}), NOW)
    with store.connect() as db:
        db.execute("UPDATE position_snapshots SET quantity='4' WHERE account_id='iis' AND instrument_id='a'")
    after = analyze(store, previous=reviews.latest('iis'))
    assert any('Количество' in c['detail'] for c in after['changes'])
    assert len(reviews.history('iis')) == 1


def settings(path):
    return Settings(invest_token='fake', account_ids=('iis',), telegram_token='', telegram_chat_id=None,
                    timezone=ZoneInfo('Europe/Moscow'), db_path=path, cbr_path=path.parent/'cbr.json',
                    startup_catchup=False, times={})


class Api:
    tz = ZoneInfo('Europe/Moscow')
    calls = 0
    def accounts(self):
        self.calls += 1
        return [{'id': 'iis', 'type': 'ACCOUNT_TYPE_TINKOFF_IIS', 'name': 'ИИС'}]
    def portfolio(self, aid):
        return {'totalAmountPortfolio': mv(1000), 'positions': [{'instrumentUid': 'a', 'instrumentType': 'bond', 'quantity': mv(1), 'currentPrice': mv(990), 'currentNkd': mv(10)}]}
    def operations(self, *args):
        return []
    def instrument(self, uid='', figi=''):
        return Instrument(uid, figi, 'Bond', 'RU000TEST', 'A', 'bond', 'RUB', {})
    def bond(self, uid):
        return meta()
    def coupons(self, *args):
        return []


def test_runner_force_metadata_due_schedule_and_lease(store):
    api = Api()
    first = run_review(settings(store.path), api=api, scan=False, enrich_issuers=False, now=NOW)
    assert first['account']['is_iis']
    second = run_review(settings(store.path), api=api, scan=False, enrich_issuers=False, now=NOW+timedelta(days=1), force=False)
    assert second['skipped'] and api.calls == 1
    run_review(settings(store.path), api=api, scan=False, enrich_issuers=False, now=NOW+timedelta(days=3), force=False)
    assert api.calls == 2
    reviews = ReviewStore(store.path)
    token = reviews.claim(NOW+timedelta(days=4))
    assert token and reviews.claim(NOW+timedelta(days=4)) is None
    assert run_review(settings(store.path), api=api, scan=False, enrich_issuers=False, now=NOW+timedelta(days=4))['busy']
    reviews.release(token)


def test_runner_failed_refresh_does_not_create_success_or_lose_error(store):
    class Broken(Api):
        def portfolio(self, aid):
            raise RuntimeError('private-token-must-not-be-stored')
    with pytest.raises(RuntimeError):
        run_review(settings(store.path), api=Broken(), scan=False, enrich_issuers=False, now=NOW)
    reviews = ReviewStore(store.path)
    assert reviews.latest('iis') is None
    assert 'RuntimeError' in reviews.job()['last_error']
    assert 'private-token' not in reviews.job()['last_error']


def test_placement_retains_risk_and_iis_fields():
    raw = dict(meta(), uid='new', name='Issue', placementDate='2026-12-01', riskLevel='RISK_LEVEL_HIGH')
    item = from_tinvest(raw, TODAY)
    assert item['for_iis'] is True and item['ratings'] and item['risk_level'] == 'RISK_LEVEL_HIGH'


def test_moex_identity_exact_match_conflicts_and_cached_grouping(store):
    from invest_blog.issuer_reference import IssuerReference
    class Response:
        url = 'https://iss.moex.com/iss/securities.json?q=RU000A000001'
        rows = [['RU000A000001', 1, 'Issuer A', '1234567890'], ['OTHER', 2, 'Wrong', '9999999999']]
        def raise_for_status(self): pass
        def json(self):
            return {'securities': {'columns': ['isin', 'emitent_id', 'emitent_title', 'emitent_inn'], 'data': self.rows}}
    class Session:
        headers = {}
        def get(self, url, **kwargs):
            assert 'Authorization' not in self.headers
            assert kwargs['params']['q'] == 'RU000A000001'
            return Response()
    reference = IssuerReference(Session())
    value = reference.lookup('RU000A000001')
    assert value['issuer_id'] == 'inn:1234567890'
    assert reference.lookup('not-an-isin') is None
    reviews = ReviewStore(store.path)
    reviews.save_issuer('RU000TEST', value, TODAY)
    with store.connect() as db:
        db.execute('UPDATE bond_metadata SET raw_json=?', (json.dumps(meta(issuer=None)),))
    report = analyze(store, issuer_data=reviews.issuer_data(TODAY))
    assert report['issuers'][0]['name'] == 'Issuer A'
    assert report['summary']['issuer_complete']
    assert not reviews.issuer_data(TODAY+timedelta(days=31))
    Response.rows.append(['RU000A000001', 2, 'Conflict', '9999999999'])
    assert reference.lookup('RU000A000001') is None


def test_failed_placement_source_never_produces_research_candidate(store):
    item = {'id': 1, 'name': 'New', 'isin': 'NEW', 'issuer_uid': 'new-issuer', 'source': 'tinvest',
            'for_iis': True, 'ratings': meta()['ratings'], 'subordinated': False, 'perpetual': False}
    report = analyze(store, placements=[item], placement_sources=[{'source': 'tinvest', 'ok': 0, 'checked_at': NOW.isoformat()}])
    assert report['candidates'][0]['status'] == 'verify'
    assert any('Источник' in r for r in report['candidates'][0]['reasons'])


def test_local_routes_protect_mutations_and_queue_background_job(store, monkeypatch):
    from fastapi import BackgroundTasks, FastAPI, HTTPException
    from invest_blog import iis_routes
    cfg = settings(store.path)
    monkeypatch.setattr(iis_routes.Settings, 'load', lambda: cfg)
    app = FastAPI()
    def guard(request):
        if not request.allowed:
            raise HTTPException(403)
    iis_routes.register_iis_routes(app, store.path, guard)
    routes = {r.path: r.endpoint for r in app.routes}
    class Request:
        def __init__(self, data, allowed=True): self.data, self.allowed = data, allowed
        async def json(self): return self.data
    def finish(coro):
        # These handlers have no asynchronous I/O except Request.json. Run their
        # immediate coroutines without a real event loop or network sockets.
        try: coro.send(None)
        except StopIteration as done: return done.value
        raise AssertionError('Unexpected async I/O in unit endpoint')
    with pytest.raises(HTTPException) as error:
        finish(routes['/api/iis-review/policy'](Request({}, False)))
    assert error.value.status_code == 403
    payload = {'issuer_limit_pct': 12, 'high_risk_limit_pct': 10, 'rating_max_days': 180}
    assert finish(routes['/api/iis-review/policy'](Request(payload))) == payload
    report = routes['/api/iis-review']('iis')
    assert report['account_id'] == 'iis' and report['summary']['bond_count'] == 2
    with pytest.raises(HTTPException): routes['/api/iis-review']('not-configured')
    background = BackgroundTasks()
    accepted = finish(routes['/api/iis-review/run'](Request({'account_id': 'iis'}), background))
    assert accepted['accepted'] and len(background.tasks) == 1
    assert 'Разбор ИИС' in routes['/iis']()


def test_forced_review_refreshes_even_fresh_metadata(store):
    class Changed(Api):
        def bond(self, uid): return meta(rating='B(RU)')
    report = run_review(settings(store.path), api=Changed(), scan=False, enrich_issuers=False, now=NOW)
    assert report['bonds'][0]['risk'] == 'high'
