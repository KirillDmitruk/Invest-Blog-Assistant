"""Strictly read-only futures market gateway. No account IDs or order methods."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from .model import quotation, iso_dt
import time
import requests
from .tinvest import BASE


ALLOWED = {('InstrumentsService', name) for name in ('FindInstrument', 'FutureBy', 'GetFuturesMargin', 'Futures')} | {
    ('MarketDataService', name) for name in ('GetCandles', 'GetLastPrices', 'GetTradingStatus')}


class FuturesMarket:
    def __init__(self, token, tz, api=None):
        if not token and api is None:
            raise ValueError('Для рыночных данных нужен существующий read-only TINVEST_TOKEN.')
        self.api = api
        self.session = requests.Session()
        self.session.headers.update({'Authorization': 'Bearer '+token, 'Content-Type': 'application/json'})
        self.last_request = 0

    def read(self, service, method, payload):
        if (service, method) not in ALLOWED:
            raise ValueError('Торговый шлюз этой версии разрешает только чтение рыночных данных.')
        if self.api is not None:
            return self.api.call(service, method, payload)
        delay = .25-(time.monotonic()-self.last_request)
        if delay > 0:
            time.sleep(delay)
        self.last_request = time.monotonic()
        try:
            response = self.session.post(BASE+service+'/'+method, json=payload, timeout=(5, 12), allow_redirects=False)
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError
            return result
        except Exception:
            raise ValueError('Не удалось прочитать '+method+' в T-Инвестициях. Проверь подключение и read-only токен.') from None

    def search(self, query):
        result = self.read('InstrumentsService', 'FindInstrument',
                           {'query': query, 'instrumentKind': 'INSTRUMENT_TYPE_FUTURES', 'apiTradeAvailableFlag': True})
        return [{'uid': r['uid'], 'ticker': r.get('ticker', ''), 'name': r.get('name', '')}
                for r in result.get('instruments', []) if r.get('uid')
                and r.get('instrumentType', '').lower() == 'futures'][:20]

    def universe(self, clock):
        """Bounded diverse near-expiry sample; not a claim to scan the whole market."""
        result = self.read('InstrumentsService', 'Futures', {'instrumentStatus': 'INSTRUMENT_STATUS_BASE'})
        rows = []
        for r in result.get('instruments', []):
            try:
                expiry = iso_dt(r.get('expirationDate') or r.get('lastTradeDate'))
                if not r.get('apiTradeAvailableFlag') or expiry <= clock+timedelta(days=1):
                    continue
                rows.append((expiry, r))
            except (ValueError, TypeError):
                continue
        rows.sort(key=lambda item: (item[0], item[1].get('ticker', '')))
        chosen, seen = [], set()
        for expiry, r in rows:
            base = r.get('basicAsset') or r.get('ticker', '')[:-2]
            if not base or base in seen or not r.get('uid'):
                continue
            seen.add(base)
            chosen.append(r['uid'])
            if len(chosen) == 8:
                break
        return {'uids': chosen, 'eligible_contracts': len(rows),
                'selection': 'До 8 разных базовых активов, ближайшая действующая серия; без ранжирования по доходности.'}

    def contract(self, uid):
        future = self.read('InstrumentsService', 'FutureBy', {'idType': 'INSTRUMENT_ID_TYPE_UID', 'id': uid}).get('instrument')
        margin = self.read('InstrumentsService', 'GetFuturesMargin', {'instrumentId': uid})
        if not future:
            raise ValueError('Не удалось получить карточку фьючерса.')
        buy, sell = margin.get('initialMarginOnBuy'), margin.get('initialMarginOnSell')
        if not buy or not sell or buy.get('currency', '').lower() != 'rub' or sell.get('currency', '').lower() != 'rub':
            raise ValueError('В первой версии нужны подтверждённые параметры фьючерса в RUB.')
        tick = quotation(margin.get('minPriceIncrement'))
        tick_value = quotation(margin.get('minPriceIncrementAmount'))
        if any(not x.is_finite() or x <= 0 for x in (tick, tick_value, quotation(buy), quotation(sell))):
            raise ValueError('Отсутствуют положительные шаг цены, стоимость шага или ГО.')
        # A lot must be a single futures contract; do not silently miscount multipliers.
        if int(future.get('lot', 1)) != 1:
            raise ValueError('Лот этого фьючерса требует отдельного учёта; в симулятор не добавлен.')
        expires = future.get('expirationDate') or future.get('lastTradeDate')
        if not expires or iso_dt(expires).tzinfo is None:
            raise ValueError('Нет подтверждённой даты экспирации контракта.')
        return {'uid': uid, 'ticker': future.get('ticker', ''), 'name': future.get('name', ''),
                'tick': str(tick), 'tick_value': str(tick_value), 'margin_long': str(quotation(buy)),
                'margin_short': str(quotation(sell)), 'expires_at': future.get('expirationDate') or future.get('lastTradeDate'),
                'source': 't-invest', 'as_of': datetime.now(timezone.utc).isoformat()}

    def snapshot(self, uid, clock=None):
        supplied_clock = clock
        contract = self.contract(uid)
        prices = self.read('MarketDataService', 'GetLastPrices', {'instrumentId': [uid]}).get('lastPrices', [])
        price = next((r for r in prices if r.get('instrumentUid') == uid), None)
        if not price or not price.get('time') or quotation(price.get('price')) <= 0:
            raise ValueError('Нет последней цены с временем котировки.')
        clock = supplied_clock or datetime.now(timezone.utc)
        stamp = iso_dt(price['time'])
        if stamp.tzinfo is None or not timedelta(seconds=-30) <= clock - stamp <= timedelta(minutes=15):
            raise ValueError('Последняя котировка устарела или имеет неверное время. Сделки пропущены.')
        status = self.read('MarketDataService', 'GetTradingStatus', {'instrumentId': uid})
        tradeable = status.get('tradingStatus') == 'SECURITY_TRADING_STATUS_NORMAL_TRADING' and status.get('apiTradeAvailableFlag') is True
        candles = self.read('MarketDataService', 'GetCandles', {
            'instrumentId': uid, 'from': (clock-timedelta(days=7)).isoformat(), 'to': clock.isoformat(),
            'interval': 'CANDLE_INTERVAL_15_MIN', 'limit': 1000}).get('candles', [])
        completed = {}
        for c in candles:
            if c.get('isComplete') is not True or not c.get('time'):
                continue
            ts = iso_dt(c['time'])
            # A completed 15-minute candle cannot contain future information.
            if ts.tzinfo is None or ts+timedelta(minutes=15) > clock:
                continue
            values = {k: quotation(c.get(k)) for k in ('open', 'high', 'low', 'close')}
            if any(not v.is_finite() or v <= 0 for v in values.values()) or values['high'] < max(values.values()) or values['low'] > min(values.values()):
                continue
            completed[c['time']] = {**{k: str(v) for k, v in values.items()}, 'time': c['time']}
        bars = [completed[k] for k in sorted(completed)]
        return {'contract': contract, 'price': str(quotation(price['price'])), 'quoted_at': price['time'],
                'tradeable': tradeable, 'candles': bars}


def indicators(bars):
    if len(bars) < 35:
        raise ValueError('Нужно не менее 35 завершённых 15-минутных свечей.')
    closes = [Decimal(c['close']) for c in bars]
    ranges = [max(Decimal(c['high'])-Decimal(c['low']), abs(Decimal(c['high'])-closes[i-1]),
                  abs(Decimal(c['low'])-closes[i-1])) for i, c in enumerate(bars) if i]
    return {'last_close': str(closes[-1]), 'sma_fast': str(sum(closes[-10:])/10),
            'sma_slow': str(sum(closes[-30:])/30), 'atr': str(sum(ranges[-14:])/14),
            'breakout_high': str(max(Decimal(c['high']) for c in bars[-21:-1])),
            'breakout_low': str(min(Decimal(c['low']) for c in bars[-21:-1])), 'candle_at': bars[-1]['time']}
