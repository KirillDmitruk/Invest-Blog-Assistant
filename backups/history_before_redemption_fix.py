"""Historical portfolio estimate from executed operations + daily closing prices.

This is NOT a broker-confirmed daily NAV and NOT investment return.
No extrapolation over missing prices; incomplete dates remain absent.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import bisect
import json
import logging

log = logging.getLogger(__name__)

from .model import quotation
from .portfolio_store import PortfolioStore, _now
from .tinvest import TInvestError

BUY_KINDS = {
    'OPERATION_TYPE_BUY', 'OPERATION_TYPE_BUY_CARD', 'OPERATION_TYPE_BUY_MARGIN',
    'OPERATION_TYPE_DELIVERY_BUY', 'OPERATION_TYPE_PRIMARY_ORDER',
}
SELL_KINDS = {
    'OPERATION_TYPE_SELL', 'OPERATION_TYPE_SELL_CARD', 'OPERATION_TYPE_SELL_MARGIN',
    'OPERATION_TYPE_DELIVERY_SELL',
}
# Security transfers/corporate actions need special treatment, not invented as trades.
UNSUPPORTED_SECURITY_KINDS = {
    'OPERATION_TYPE_INPUT_SECURITIES','OPERATION_TYPE_OUTPUT_SECURITIES',
    'OPERATION_TYPE_TRANS_IIS_BS','OPERATION_TYPE_TRANS_BS_BS',
    'OPERATION_TYPE_OPTION_EXPIRATION','OPERATION_TYPE_FUTURE_EXPIRATION',
    'OPERATION_TYPE_DFA_REDEMPTION',
}


def _D(value):
    return Decimal(str(value))


def _executed_quantity(operation: dict) -> Decimal:
    # GetOperationsByCursor normally reports quantityDone; older records may
    # have only quantity. Zero is not a valid completed trade quantity.
    v = operation.get('quantityDone')
    if v is None:
        v = operation.get('quantity')
    try:
        result = _D(v)
    except (ValueError, TypeError, ArithmeticError) as exc:
        raise ValueError('Количество сделки отсутствует') from exc
    if not result.is_finite() or result <= 0:
        raise ValueError('Некорректное количество исполненных бумаг')
    return result


def _reverse_day(holdings: dict, operations: list, *, day: date, account_id: str) -> tuple[dict, Decimal]:
    """Reverse *all* same-day trades atomically, using executed share counts.

    A stock sold and bought again during one day may go negative if operations
    are processed in an arbitrary same-day order. Only end-of-day holdings
    matter for the daily NAV we reconstruct.
    """
    changes = defaultdict(Decimal)
    cash_delta = Decimal(0)
    for kind, op_day, currency, amount, uid, raw in operations:
        if currency != 'RUB':
            raise ValueError(f'{day}: операция в {currency} на счёте …{account_id[-4:]} — нужен FX')
        if kind in UNSUPPORTED_SECURITY_KINDS:
            raise ValueError(f'{day}: операция {kind} требует отдельного учёта движения бумаг')
        if kind == 'OPERATION_TYPE_BOND_REPAYMENT_FULL':
            # This operation often does not carry quantityDone/quantity at all;
            # it cannot be reversed by guessing how many bonds matured.
            raise ValueError(f'{day}: полное погашение облигации …{str(uid)[-8:]} '
                             'требует подтверждённого количества бумаг')
        cash_delta -= amount
        if kind in BUY_KINDS | SELL_KINDS:
            if not uid:
                raise ValueError(f'{day}: сделка без идентификатора инструмента')
            qty = _executed_quantity(raw)
            changes[uid] += -qty if kind in BUY_KINDS else qty

    updated = dict(holdings)
    for uid, delta in changes.items():
        before = updated.get(uid, Decimal(0))
        after = before + delta
        if after < 0:
            operation_counts = defaultdict(Decimal)
            for kind, _, _, _, op_uid, raw in operations:
                if op_uid == uid and kind in BUY_KINDS | SELL_KINDS:
                    operation_counts[kind] += _executed_quantity(raw)
            details = ', '.join(f'{kind}={quantity}' for kind, quantity in sorted(operation_counts.items()))
            raise ValueError(
                f'{day}: отрицательный остаток бумаги …{str(uid)[-8:]} '
                f'на счёте …{account_id[-4:]}: было {before}, '
                f'изменение {delta}, стало {after}; операции за день: {details}. '
                'Возможны неполная история, переводы или корпоративные события.'
            )
        updated[uid] = after
    return updated, cash_delta


def _preflight_quantities(states: dict, *, anchor: date, first: date) -> tuple[date, str | None]:
    """Find earliest *safe* portfolio date without requesting market candles.

    When an anomaly occurs while reversing day D, EOD D is still reachable,
    whereas EOD D-1 is not. The safely reconstructable suffix is [D, anchor).
    """
    positions = {aid: dict(state['holdings']) for aid, state in states.items()}
    current = anchor
    while current > first:
        # Evaluate every account before mutating the simulation's balances.
        updated = {}
        try:
            for aid, state in states.items():
                updated[aid], _ = _reverse_day(positions[aid],
                    state['ops_by_day'].get(current, []), day=current, account_id=aid)
        except ValueError as exc:
            return current, str(exc)
        positions = updated
        current -= timedelta(days=1)
    return first, None


def _is_rub_cash_position(position) -> bool:
    """Only an identifiable RUB cash row can be represented by cash_rub.

    GetPortfolio includes cash in totalAmountCurrencies and may repeat RUB
    as a position. The `currency` column here is the *price quote currency*,
    not necessarily the denomination of the cash balance (USD often quotes
    in RUB). Never skip an unidentified or foreign-currency position.
    """
    identity = {
        str(position[field] or '').strip().upper()
        for field in ('instrument_id', 'ticker')
    }
    name = str(position['name'] or '').strip().casefold()
    is_rub = (bool(identity & {'RUB000UTSTOM', 'RUB'})
              or name in {'российский рубль', 'российские рубли', 'рубль', 'рубли', 'russian ruble', 'russian rubles'})
    if not is_rub:
        return False
    if str(position['currency'] or '').upper() != 'RUB':
        return False
    price = position['price']
    return price is not None and _D(price) == Decimal('1')


def _rub_nominal(value) -> tuple[Decimal | None, str]:
    """Only a *stated* positive RUB MoneyValue is safe for bond prices.

    Missing currency/nominal is not implicitly RUB: `money()` defaults to RUB
    for absent fields and would silently turn incomplete metadata into facts.
    """
    if not isinstance(value, dict) or not value:
        return None, 'отсутствует'
    currency = str(value.get('currency') or '').upper()
    if currency != 'RUB':
        return None, f'валюта номинала {currency or "не указана"}'
    try:
        amount = quotation(value)
    except (TypeError, ValueError, ArithmeticError):
        return None, 'некорректное число'
    if not amount.is_finite() or amount <= 0:
        return None, f'неположительное значение {amount}'
    return amount, ''


def _resolve_bond_nominal(bond: dict) -> tuple[Decimal | None, str]:
    """Prefer current face; fallback to initial ONLY for non-amortizing bonds.

    A redeemed bond may have nominal=0 even though its historical price
    was expressed as a percentage of its original face value.
    """
    current, reason = _rub_nominal(bond.get('nominal'))
    if current is not None:
        return current, 'nominal'
    if bond.get('amortizationFlag') is not False:
        return None, (f'nominal: {reason}; начальный номинал нельзя использовать '
                      'без подтверждения отсутствия амортизации')
    initial, initial_reason = _rub_nominal(bond.get('initialNominal'))
    if initial is not None:
        # A nonzero explicitly foreign-currency `nominal` contradicts a RUB
        # initial nominal; never switch denominators silently.
        raw_current = bond.get('nominal')
        if isinstance(raw_current, dict) and raw_current.get('currency') not in (None, '', 'RUB'):
            return None, f'nominal: {reason}; противоречивые валюты номиналов'
        return initial, 'initialNominal'
    return None, f'nominal: {reason}; initialNominal: {initial_reason}'


def _covering_interval(ranges, first: date, last: date) -> bool:
    """Require continuous imported coverage, not only first and last dates."""
    cursor = first
    for a,b in sorted((date.fromisoformat(r['first_day']), date.fromisoformat(r['last_day'])) for r in ranges):
        if a > cursor:
            return False
        if b >= cursor:
            cursor = b + timedelta(days=1)
        if cursor > last:
            return True
    return False


class HistoricalBackfill:
    def __init__(self, api, accounts: tuple[str,...], store: PortfolioStore):
        self.api, self.accounts, self.store = api, tuple(accounts), store

    def run(self, *, days: int = 365, partial: bool = False) -> dict:
        if not 2 <= days <= 365:
            raise ValueError('Для backfill укажи от 2 до 365 дней')
        warnings: set[str] = set()
        with self.store.connect() as db:
            anchors = {aid: db.execute('''SELECT * FROM portfolio_snapshots
                    WHERE account_id=? ORDER BY day DESC LIMIT 1''',(aid,)).fetchone() for aid in self.accounts}
            if any(v is None for v in anchors.values()):
                raise ValueError('Нет текущего снимка по всем счетам. Выполни sync --days 365.')
            anchor = date.fromisoformat(next(iter(anchors.values()))['day'])
            if any(a['day'] != anchor.isoformat() for a in anchors.values()):
                raise ValueError('Последние снимки счетов сделаны в разные дни; сначала обнови sync.')
            first = anchor-timedelta(days=days-1)
            account_state = {}
            instrument_ids=set()
            for aid, a in anchors.items():
                ranges=db.execute('SELECT first_day,last_day FROM operation_import_ranges WHERE account_id=?',(aid,)).fetchall()
                if not _covering_interval(ranges, first, anchor):
                    raise ValueError(f'Не хватает сплошной истории операций для счёта …{aid[-4:]}: '
                                     f'{first} — {anchor}. Сначала выполни sync --days {days}.')
                other=_D(a['other_rub'])
                if abs(other) > Decimal('0.01'):
                    raise ValueError('В портфеле есть активы вне поддерживаемых классов (other_rub). '
                                     'Ретроспективная оценка может быть неверной; восстановление остановлено.')
                holdings={}
                kinds={}
                for p in db.execute('''SELECT * FROM position_snapshots WHERE account_id=? AND day=?''',
                                    (aid,anchor.isoformat())):
                    kind=str(p['kind']).lower()
                    if kind == 'currency':
                        # Already included in totalAmountCurrencies -> cash_rub.
                        # Counting a RUB cash position again would double NAV.
                        if _is_rub_cash_position(p):
                            continue
                        ident = str(p['ticker'] or p['name'] or p['instrument_id'][-8:])
                        raise ValueError(
                            f'Валютная позиция {ident!r} на счёте …{aid[-4:]}: '
                            'нельзя безопасно восстановить RUB-стоимость без '
                            'исторических FX-курсов. Поле currency у позиции '
                            'описывает валюту цены, а не обязательно сам актив.'
                        )
                    if kind not in ('bond','share','etf'):
                        raise ValueError(f'Неподдерживаемый тип позиции {kind} для …{aid[-4:]}')
                    if p['currency'] != 'RUB':
                        raise ValueError('Есть позиции с ценой не в RUB: без исторического FX-курса '
                                         'рублёвая стоимость не может быть восстановлена.')
                    holdings[p['instrument_id']]=_D(p['quantity'])
                    kinds[p['instrument_id']]=kind
                    instrument_ids.add(p['instrument_id'])
                ops=[]
                for row in db.execute('''SELECT kind,executed_at,instrument_id,currency,amount,raw_json
                    FROM broker_operations WHERE account_id=? AND substr(executed_at,1,10)>=?
                    AND substr(executed_at,1,10)<=? ORDER BY executed_at DESC''',
                                      (aid,(first-timedelta(days=1)).isoformat(),(anchor+timedelta(days=1)).isoformat())):
                    op=json.loads(row['raw_json'])
                    # Snapshot can be taken before the end of the local day; never reverse later operations.
                    stamp=datetime.fromisoformat(row['executed_at'].replace('Z','+00:00'))
                    snapshot_at=datetime.fromisoformat(a['captured_at'].replace('Z','+00:00'))
                    if stamp.astimezone(timezone.utc) > snapshot_at.astimezone(timezone.utc):
                        warnings.add('Операции позже момента снимка не использовались')
                        continue
                    # The broker timestamps are UTC. Reverse by the SAME local
                    # trading day used by sync, not stamp.date() in UTC.
                    broker_day=stamp.astimezone(self.api.tz).date() if hasattr(self.api,'tz') else stamp.date()
                    if not first <= broker_day <= anchor:
                        continue
                    ops.append((row['kind'],broker_day,row['currency'],_D(row['amount']),row['instrument_id'],op))
                    if row['instrument_id'] and row['kind'] in BUY_KINDS | SELL_KINDS:
                        instrument_ids.add(row['instrument_id'])
                account_state[aid]={'cash':_D(a['cash_rub']),'holdings':holdings,'kinds':kinds,'ops':ops}

        # Resolve identifiers and bond conversion factors before mutating any database state.
        profiles={}
        aliases={}
        for uid in instrument_ids:
            # Reject ambiguous records rather than assigning made-up prices.
            info=self.api.instrument(uid=uid)
            canonical=info.uid or uid
            aliases[uid]=canonical
            kind=info.kind.lower().replace('instrument_type_', '')
            if kind not in ('bond','share','etf'):
                raise ValueError(f'Историческая позиция {uid[-8:]}: неподдерживаемый тип {kind}')
            if info.currency.upper() != 'RUB':
                raise ValueError(f'Историческая позиция {uid[-8:]} не в RUB: нужен исторический FX')
            nominal=None
            if kind == 'bond':
                try:
                    b=self.api.bond(canonical)
                except TInvestError as exc:
                    # Old/delisted securities can disappear from BondBy.
                    # An authentication error or transient API failure is NOT
                    # the same thing as missing instrument metadata.
                    if 'HTTP 404' not in str(exc):
                        raise
                    b={}
                nominal, origin=_resolve_bond_nominal(b)
                ident=f'{info.ticker or info.name or "облигация"} (…{uid[-8:]})'
                if nominal is None:
                    warnings.add(f'Невозможно оценить {ident}: {origin}. '
                                 'Даты с этой позицией будут пропущены')
                elif origin=='initialNominal':
                    warnings.add(f'Для {ident} использован initialNominal вместо '
                                 'отсутствующего/нулевого текущего номинала; '
                                 'применимо только к неамортизируемому выпуску')
                if b.get('amortizationFlag') and nominal is not None:
                    warnings.add('Амортизируемые облигации рассчитаны по текущему номиналу: '
                                 'значения до амортизации могут быть занижены')
            profiles[canonical]={'kind':kind,'nominal':nominal}
        # Alias map supports FIGI records that resolved to a UID.
        for aid, state in account_state.items():
            new=defaultdict(Decimal)
            for uid,qty in state['holdings'].items():
                new[aliases[uid]] += qty
            state['holdings']=new
            normalized=[]
            for kind,day,cur,amount,uid,raw in state['ops']:
                if kind in BUY_KINDS | SELL_KINDS and not uid:
                    raise ValueError('Сделка без идентификатора инструмента; восстановление остановлено')
                normalized.append((kind,day,cur,amount,aliases.get(uid,uid),raw))
            state['ops_by_day']=defaultdict(list)
            for op in normalized:
                state['ops_by_day'][op[1]].append(op)

        # Validate holdings BEFORE downloading historical candles. A mismatch
        # should not waste dozens of API calls or be silently turned into 0.
        cutoff, problem = _preflight_quantities(account_state, anchor=anchor, first=first)
        if problem:
            if not partial:
                raise ValueError(
                    f'Проверка истории до загрузки котировок: {problem} '
                    'Для восстановления только проверяемого интервала используй '
                    'backfill --days N --partial.'
                )
            warnings.add(f'История ограничена датой {cutoff}: {problem}')
            if cutoff >= anchor:
                raise ValueError(f'Нет безопасного интервала для восстановления: {problem}')
        # Price only assets that can occur within the verified suffix.
        # Older, already-sold securities outside that suffix do not need candles.
        needed=set()
        for state in account_state.values():
            needed.update(uid for uid, qty in state['holdings'].items() if qty != 0)
            for op_day, operations in state['ops_by_day'].items():
                if op_day > cutoff:
                    needed.update(uid for kind, _, _, _, uid, _ in operations
                                  if uid and kind in BUY_KINDS | SELL_KINDS)
        profiles={uid: profile for uid, profile in profiles.items() if uid in needed}
        from_day=cutoff-timedelta(days=35)
        end_day=anchor-timedelta(days=1)
        quotes={}
        for i,(uid, profile) in enumerate(profiles.items(),start=1):
            log.info('Загрузка исторических котировок: %d/%d, инструмент …%s',
                     i,len(profiles),uid[-8:])
            quotes[uid]={}
            if profile['kind']=='bond' and profile['nominal'] is None:
                # The security can still have zero holdings on earlier days.
                # Avoid fetching prices that could not be converted into RUB.
                continue
            candles=self.api.daily_candles(uid,from_day,end_day)
            for c in candles:
                if not c.get('isComplete',True):
                    continue
                close=quotation(c.get('close'))
                if close<=0:
                    continue
                qday=c['time'][:10]
                quote_unit=str(c.get('priceCurrency') or 'RUB').strip().upper()
                if profile['kind']=='bond':
                    # T-Invest returns bond prices in points (% of face value).
                    # The GetCandles response can mark the unit as 'PT.', not
                    # RUB. This is a quotation unit, NOT a foreign currency.
                    # Only bonds with an explicitly verified RUB nominal reach
                    # this branch (see _resolve_bond_nominal above).
                    if quote_unit not in ('RUB', 'PT.', 'PT'):
                        raise ValueError(
                            f'Котировка облигации {uid[-8:]} в единицах '
                            f'{quote_unit}: нужна отдельная конвертация'
                        )
                    px=close*profile['nominal']/Decimal(100)
                else:
                    # Shares and ETFs must actually be quoted in RUB. Never
                    # treat 'PT.' as rubles for a non-bond instrument.
                    if quote_unit!='RUB':
                        raise ValueError(
                            f'Котировка {uid[-8:]} в валюте/единицах '
                            f'{quote_unit}: нужна конвертация'
                        )
                    px=close
                quotes[uid][qday]=px
            if not quotes[uid]:
                warnings.add(f'Нет исторических цен для инструмента …{uid[-8:]}')
        # End-of-day account balances calculated backward from the broker snapshot.
        results=[]
        missed=0
        truncated=(cutoff-first).days*len(self.accounts)
        current=anchor
        while current > cutoff:
            for aid, state in account_state.items():
                new_holdings, cash_delta = _reverse_day(state['holdings'],
                    state['ops_by_day'].get(current, []), day=current, account_id=aid)
                state['holdings'] = new_holdings
                state['cash'] += cash_delta
            current -= timedelta(days=1)
            date_s=current.isoformat()
            for aid,state in account_state.items():
                asset_value=Decimal(0)
                valid=True
                for uid,qty in state['holdings'].items():
                    if qty == 0:
                        continue
                    prices=quotes.get(uid,{})
                    keys=sorted(prices)
                    ix=bisect.bisect_right(keys,date_s)-1
                    if ix<0:
                        valid=False
                        break
                    latest_day=date.fromisoformat(keys[ix])
                    if (current-latest_day).days > 35:
                        valid=False
                        break
                    asset_value += qty*prices[keys[ix]]
                if valid:
                    total=state['cash']+asset_value
                    if total<0:
                        warnings.add('Некоторые расчётные значения оказались отрицательными и пропущены')
                        missed+=1
                    else:
                        results.append((date_s,aid,str(total)))
                else:
                    missed+=1
        if truncated:
            warnings.add(f'Не восстановлено {truncated} дней/счетов до {cutoff} '
                         'из-за расхождения истории операций')
        if missed:
            warnings.add(f'Пропущено {missed} дней/счетов без достаточных исторических цен')
        warnings.add('Исторические значения — оценки по ценам закрытия; НКД не восстановлен')
        warnings.add('Это график стоимости, а не доходности; пополнения и выводы включены')
        if not results:
            details = [w for w in sorted(warnings) if w.startswith('Невозможно оценить ')]
            explanation = '; '.join(details[:3]) if details else 'нет достаточно полных котировок/операций'
            raise ValueError('Не удалось восстановить ни одной исторической точки: '
                             f'{explanation}. Предыдущая история в SQLite сохранена.')
        with self.store.connect() as db:
            for aid in self.accounts:
                db.execute('''DELETE FROM reconstructed_portfolio_history WHERE account_id=? AND day>=? AND day<?''',
                           (aid,first.isoformat(),anchor.isoformat()))
            db.executemany('''INSERT INTO reconstructed_portfolio_history VALUES (?,?,?,?,?)''',
                           [(day,aid,value,'estimated',_now()) for day,aid,value in results])
            db.execute('''INSERT INTO history_backfill_runs(ran_at,first_day,last_day,points,warnings_json)
                          VALUES (?,?,?,?,?)''',(_now(),first.isoformat(),anchor.isoformat(),len(results),
                                                 json.dumps(sorted(warnings),ensure_ascii=False)))
        return {'period':f'{cutoff} — {anchor}', 'points':len(results),
                'warnings':sorted(warnings), 'days_missing':missed+truncated}
