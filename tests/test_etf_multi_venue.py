"""Cross-mode ETF history must aggregate verified economic exposure only."""
from datetime import date
from decimal import Decimal
from zoneinfo import ZoneInfo
import pytest

from invest_blog.history import HistoricalBackfill, _verified_etf_groups
from invest_blog.model import Instrument
from invest_blog.portfolio_store import PortfolioStore


def mv(v):
    return {'units': str(v), 'nano': 0, 'currency': 'RUB'}


def etf(uid, *, asset='ASSET-X', isin='RU000A1014L8', ticker='LQDT',
        class_code='TQTF', exchange='REAL_EXCHANGE_MOEX', lot=1):
    return Instrument(uid, '', 'ВИМ Ликвидность', isin, ticker, 'etf', 'RUB', {
        'assetUid': asset, 'positionUid': 'POSITION-'+uid, 'classCode': class_code,
        'realExchange': exchange, 'lot': lot,
    })


class ETFMarket:
    tz = ZoneInfo('Europe/Moscow')

    def __init__(self, *, different_asset=False, different_isin=False):
        self.infos = {
            'UID-TQTF': etf('UID-TQTF'),
            'UID-SPBRU': etf('UID-SPBRU',
                             asset='ANOTHER-ASSET' if different_asset else 'ASSET-X',
                             isin='RU000A999999' if different_isin else 'RU000A1014L8',
                             class_code='SPBRU', exchange='REAL_EXCHANGE_RTS'),
        }
        self.requested = []

    def instrument(self, uid='', figi=''):
        return self.infos[uid or figi]

    def daily_candles(self, uid, start, end):
        self.requested.append(uid)
        # Different venue, same economic asset and same price: no dual counting.
        return [
            {'time':f'2026-10-{day}T00:00:00Z', 'close':mv(2),
             'priceCurrency':'RUB','isComplete':True}
            for day in ('24', '25', '26', '27')
        ]


def trade(ident, day, typ, uid, qty, value):
    return {
        'id': ident, 'date':f'2026-10-{day}T08:00:00Z',
        'type':typ, 'instrumentUid':uid, 'quantityDone':qty,
        'payment':mv(value),
    }


def setup_portfolio(db):
    # Zero current holding. At 24th EOD there were 72 ETF units.
    # 25th: buy 107; 26th buy 42; 27th sell 221; 28th: zero.
    portfolio = {'totalAmountPortfolio':mv(1000), 'totalAmountBonds':mv(0),
                 'totalAmountShares':mv(0), 'totalAmountEtf':mv(0),
                 'totalAmountCurrencies':mv(1000)}
    operations = [
        trade('b107', '25', 'OPERATION_TYPE_BUY', 'UID-TQTF', 107, -107),
        trade('b42', '26', 'OPERATION_TYPE_BUY', 'UID-SPBRU', 42, -42),
        trade('s221', '27', 'OPERATION_TYPE_SELL', 'UID-SPBRU', 221, 221),
    ]
    db.save_batch(date(2026,10,28),
                  [{'account_id':'acct', 'portfolio':portfolio, 'positions':[]}],
                  [('acct',op) for op in operations], [],
                  coverage_ranges=[('acct',date(2026,10,24),date(2026,10,28))])


def test_verified_etf_group_uses_same_asset_isin_lot_and_currency():
    infos = {'A':etf('A'), 'B':etf('B', class_code='SPBRU', exchange='REAL_EXCHANGE_RTS')}
    replacements, groups = _verified_etf_groups(infos)
    assert replacements['A'] == replacements['B']
    assert groups[replacements['A']] == ['A','B']
    for changed in (
        etf('B', asset='other'), etf('B', isin='RU000A555555'),
        etf('B', lot=10), etf('B', ticker='OTHER'),
    ):
        replacements, groups = _verified_etf_groups({'A':infos['A'], 'B':changed})
        assert replacements == {}
        assert groups == {}


def test_multi_venue_etf_backfill_reconstructs_start_72_units(tmp_path):
    store=PortfolioStore(tmp_path/'multi_venue.sqlite3')
    setup_portfolio(store)
    api=ETFMarket()
    result=HistoricalBackfill(api,('acct',),store).run(days=5)
    assert result['points']==4
    assert set(api.requested)=={'UID-TQTF','UID-SPBRU'}
    values={row['day']:Decimal(str(row['total']))
            for row in store.get_dashboard_data()['history_estimated']}
    # On 24th position 72, 25th 179, 26th 221, 27th zero.
    # Cash is reversed using actual broker payments rather than assumed values.
    assert values == {
        '2026-10-24':Decimal('928')+Decimal('144'),
        '2026-10-25':Decimal('821')+Decimal('358'),
        '2026-10-26':Decimal('779')+Decimal('442'),
        '2026-10-27':Decimal('1000'),
    }
    assert any('агрегированная оценка' in w for w in result['warnings'])


@pytest.mark.parametrize('flag', ['different_asset','different_isin'])
def test_different_etfs_are_never_auto_merged(tmp_path, flag):
    store=PortfolioStore(tmp_path/f'{flag}.sqlite3')
    setup_portfolio(store)
    api=ETFMarket(**{flag:True})
    with pytest.raises(ValueError,match='отрицательный остаток'):
        HistoricalBackfill(api,('acct',),store).run(days=5)
    assert api.requested == []
