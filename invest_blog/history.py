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
REDEMPTION_FULL = 'OPERATION_TYPE_BOND_REPAYMENT_FULL'
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


def _event_date(event: dict, key: str) -> date | None:
    value = event.get(key)
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _event_redemption_count(amount: Decimal, day: date, uid: str,
                            events: list[dict]) -> Decimal | None:
    """Use verified per-bond principal, never issue-wide eventTotalVol.

    Match payout dates first; eventDate is a fallback for instruments whose
    repayment event precedes the cash credit. If multiple candidate events
    match, refuse to guess. Whole-unit ratio must be accurate to 1 kopeck.
    """
    near = []
    for event in events:
        if not isinstance(event, dict):
            continue
        typ = event.get('eventType')
        if typ not in (None, '', 'EVENT_TYPE_MTY', 3):
            continue
        per_bond = event.get('payOneBond')
        if not isinstance(per_bond, dict) or str(per_bond.get('currency') or '').upper() != 'RUB':
            continue
        try:
            unit = quotation(per_bond)
        except (TypeError, ValueError, ArithmeticError):
            continue
        if not unit.is_finite() or unit <= 0:
            continue
        priority = None
        for key in ('realPayDate', 'payDate', 'eventDate'):
            when = _event_date(event, key)
            if when is None:
                continue
            offset = abs((day - when).days)
            if offset <= 7:
                # Real settlement/payment dates are more reliable than event date.
                score = (offset, 0 if key == 'realPayDate' else 1 if key == 'payDate' else 2)
                if priority is None or score < priority:
                    priority = score
        if priority is None:
            continue
        qty = amount / unit
        whole = qty.to_integral_value()
        if whole <= 0 or abs(amount - whole * unit) > Decimal('0.01'):
            continue
        # Equal score/amount may still be distinct corporate actions. Keep all.
        near.append((priority, event.get('eventNumber'), unit, whole))
    if not near:
        return None
    near.sort(key=lambda x: x[0])
    best_score = near[0][0]
    best = [r for r in near if r[0] == best_score]
    if len(best) != 1:
        raise ValueError(f'{day}: для погашения …{uid[-8:]} найдено '
                         'несколько равнозначных событий GetBondEvents; '
                         'нужен брокерский отчёт')
    return best[0][3]


def _redemption_quantity(raw: dict, amount: Decimal, uid: str,
                         profile: dict | None, day: date) -> Decimal:
    """Recover principal-redemption units without inventing a quantity.

    T-Invest does not generally populate quantity fields on bond redemptions.
    The received principal can establish the count ONLY when the bond has an
    explicitly confirmed RUB non-amortizing face value and the payment is an
    exact positive integer multiple of that face. Never assume 1,000 RUB.
    """
    for field in ('quantityDone', 'quantity'):
        value = raw.get(field)
        if value in (None, '', 0, '0'):
            continue
        try:
            count = _D(value)
        except (TypeError, ValueError, ArithmeticError) as exc:
            raise ValueError(f'{day}: некорректное {field} у погашения …{uid[-8:]}') from exc
        if not count.is_finite() or count <= 0 or count != count.to_integral_value():
            raise ValueError(f'{day}: некорректное количество у погашения …{uid[-8:]}')
        return count

    if profile and profile.get('kind') == 'bond':
        events = profile.get('redemption_events') or []
        count = _event_redemption_count(amount, day, uid, events)
        if count is not None:
            return count

    if not profile or profile.get('kind') != 'bond' or not profile.get('redemption_unit'):
        raise ValueError(f'{day}: полное погашение облигации …{uid[-8:]} '
                         'без количества: GetBondEvents не дал однозначной выплаты '
                         'на одну бумагу, и нет подтверждённого рублёвого номинала '
                         'неамортизируемого выпуска; нужен брокерский отчёт')
    face = profile['redemption_unit']
    if not amount.is_finite() or amount <= 0:
        raise ValueError(f'{day}: полное погашение облигации …{uid[-8:]} '
                         f'с некорректной суммой {amount}')
    count = amount / face
    if count < 1 or count != count.to_integral_value():
        raise ValueError(f'{day}: полное погашение облигации …{uid[-8:]} '
                         f'на {amount} RUB не кратно номиналу {face} RUB. '
                         'Нужно количество из брокерского отчёта')
    return count


def _reverse_day(holdings: dict, operations: list, *, day: date, account_id: str,
                 profiles: dict | None = None) -> tuple[dict, Decimal]:
    """Reverse daily cash movements, trades and verified full bond redemptions.

    Intraday trades are applied as one net adjustment; this avoids transient
    negative quantities when a buy and sell happen during the same day.
    """
    changes = defaultdict(Decimal)
    cash_delta = Decimal(0)
    for kind, op_day, currency, amount, uid, raw in operations:
        if currency != 'RUB':
            raise ValueError(f'{day}: операция в {currency} на счёте …{account_id[-4:]} — нужен FX')
        if kind in UNSUPPORTED_SECURITY_KINDS:
            raise ValueError(f'{day}: операция {kind} требует отдельного учёта движения бумаг')
        if kind == REDEMPTION_FULL:
            if not uid:
                raise ValueError(f'{day}: полное погашение без идентификатора облигации')
            count = _redemption_quantity(raw, amount, str(uid),
                                         (profiles or {}).get(uid), day)
            changes[uid] += count  # EOD before redemption contains these bonds.
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
            for kind, _, _, amount, op_uid, raw in operations:
                if op_uid != uid:
                    continue
                if kind in BUY_KINDS | SELL_KINDS:
                    operation_counts[kind] += _executed_quantity(raw)
                elif kind == REDEMPTION_FULL:
                    operation_counts[kind] += _redemption_quantity(
                        raw, amount, str(uid), (profiles or {}).get(uid), day)
            details = ', '.join(f'{kind}={quantity}' for kind, quantity in sorted(operation_counts.items()))
            raise ValueError(
                f'{day}: отрицательный остаток бумаги …{str(uid)[-8:]} '
                f'на счёте …{account_id[-4:]}: было {before}, '
                f'изменение {delta}, стало {after}; операции за день: {details}. '
                'Возможны неполная история, переводы или корпоративные события.'
            )
        updated[uid] = after
    return updated, cash_delta


def _preflight_quantities(states: dict, *, anchor: date, first: date, profiles: dict | None = None) -> tuple[date, str | None]:
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
                    state['ops_by_day'].get(current, []), day=current, account_id=aid, profiles=profiles)
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



def _verified_etf_groups(infos: dict) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Group *economic exposure* of one RUB ETF across trading venues.

    This is not a custody/position transfer. We only combine a same-account
    time series after verifying assetUid, ISIN, ticker, currency, and lot=1.
    All other instruments retain their separate UIDs. The chosen quote mode
    is deterministic (regular MOEX TQTF before other trading modes).
    """
    buckets = defaultdict(list)
    for uid, info in infos.items():
        if info.kind.lower().replace('instrument_type_', '') != 'etf':
            continue
        raw = info.raw if isinstance(info.raw, dict) else {}
        asset = str(raw.get('assetUid') or '').strip()
        isin = str(info.isin or '').strip().upper()
        ticker = str(info.ticker or '').strip().upper()
        if not (asset and isin and ticker and info.currency.upper() == 'RUB'):
            continue
        try:
            if int(raw.get('lot')) != 1:
                continue
        except (ValueError, TypeError, OverflowError):
            continue
        buckets[(asset, isin, ticker)].append(uid)

    replacements: dict[str, str] = {}
    groups: dict[str, list[str]] = {}
    for (asset, isin, ticker), uids in sorted(buckets.items()):
        if len(uids) < 2:
            continue
        # Only one confirmed asset per group. Never aggregate solely by ISIN.
        group_id = f'etf-asset:{isin}:{asset}'
        def priority(uid):
            raw = infos[uid].raw
            cls = str(raw.get('classCode') or '').upper()
            exchange = str(raw.get('realExchange') or '').upper()
            return (0 if cls == 'TQTF' and 'MOEX' in exchange else 1, uid)
        groups[group_id] = sorted(uids, key=priority)
        for uid in uids:
            replacements[uid] = group_id
    return replacements, groups


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
                    if row['instrument_id'] and row['kind'] in (BUY_KINDS | SELL_KINDS | {REDEMPTION_FULL}):
                        instrument_ids.add(row['instrument_id'])
                account_state[aid]={'cash':_D(a['cash_rub']),'holdings':holdings,'kinds':kinds,'ops':ops}

        # Identify every trading mode first; only verified same-asset ETFs
        # may share an economic holding key for reconstruction.
        infos = {}
        raw_to_uid = {}
        for uid in sorted(instrument_ids):
            info = self.api.instrument(uid=uid)
            resolved_uid = info.uid or uid
            raw_to_uid[uid] = resolved_uid
            infos[resolved_uid] = info
        etf_aliases, etf_groups = _verified_etf_groups(infos)
        aliases = {raw_uid: etf_aliases.get(uid, uid)
                   for raw_uid, uid in raw_to_uid.items()}
        profiles = {}
        for resolved_uid, info in infos.items():
            uid = resolved_uid
            key = etf_aliases.get(uid, uid)
            if key in profiles:
                continue
            kind = info.kind.lower().replace('instrument_type_', '')
            if kind not in ('bond', 'share', 'etf'):
                raise ValueError(f'Историческая позиция {uid[-8:]}: неподдерживаемый тип {kind}')
            if info.currency.upper() != 'RUB':
                raise ValueError(f'Историческая позиция {uid[-8:]} не в RUB: нужен исторический FX')
            nominal=None
            redemption_unit=None
            if kind == 'bond':
                try:
                    b=self.api.bond(resolved_uid)
                except TInvestError as exc:
                    # Old/delisted securities can disappear from BondBy.
                    # An authentication error or transient API failure is NOT
                    # the same thing as missing instrument metadata.
                    if 'HTTP 404' not in str(exc):
                        raise
                    b={}
                nominal, origin=_resolve_bond_nominal(b)
                # Full maturity pays principal, but T-Invest omits unit count.
                # Payment/face is safe only for explicitly non-amortizing RUB bonds.
                if b.get('amortizationFlag') is False and nominal is not None:
                    redemption_unit=nominal
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
            profiles[key]={'kind':kind,'nominal':nominal,
                           'redemption_unit':redemption_unit,
                           'quote_uids': etf_groups.get(key, [resolved_uid])}
        for group_id, sources in etf_groups.items():
            info = infos[sources[0]]
            warnings.add(
                f'ETF {info.ticker} ({info.isin}): для расчётной стоимости ' 
                f'объединены {len(sources)} торговых UID одного подтверждённого актива. '
                'Это агрегированная оценка, а не подтверждение перевода между площадками.'
            )
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

        # Full bond repayments in GetOperationsByCursor often have no quantity.
        # Obtain the ACTUAL principal per bond from GetBondEvents (MTY), not
        # initial nominal, which may be wrong after amortizations.
        redemption_days = defaultdict(set)
        for state in account_state.values():
            for kind, op_day, _cur, _amount, uid, raw in state['ops']:
                if kind != REDEMPTION_FULL or not uid:
                    continue
                if any(raw.get(k) not in (None, '', 0, '0')
                       for k in ('quantityDone', 'quantity')):
                    continue
                redemption_days[uid].add(op_day)
        for uid, days_set in redemption_days.items():
            if uid not in profiles:
                continue
            if not hasattr(self.api, 'bond_events'):
                continue  # Existing test providers may not implement this method.
            earliest, latest = min(days_set), max(days_set)
            try:
                events = self.api.bond_events(uid, earliest - timedelta(days=14),
                                              latest + timedelta(days=14))
            except TInvestError as exc:
                if 'HTTP 404' not in str(exc):
                    raise  # Network/auth failures must not look like missing data.
                warnings.add(f'GetBondEvents не нашёл выпуск …{uid[-8:]}; '
                             'используется только подтверждённый номинал, если он есть')
                events = []
            profiles[uid]['redemption_events'] = events

        # Validate holdings BEFORE downloading historical candles. A mismatch
        # should not waste dozens of API calls or be silently turned into 0.
        cutoff, problem = _preflight_quantities(account_state, anchor=anchor, first=first, profiles=profiles)
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
        # Surface reconstructed redemption quantities: inferred holdings are
        # useful estimates but should be cross-checked with broker statements.
        for state in account_state.values():
            for kind, op_day, cur, amount, uid, raw in state['ops']:
                if kind != REDEMPTION_FULL or op_day <= cutoff:
                    continue
                if any(raw.get(field) not in (None, '', 0, '0')
                       for field in ('quantityDone', 'quantity')):
                    continue
                count = _redemption_quantity(raw, amount, uid, profiles.get(uid), op_day)
                event_count = _event_redemption_count(amount, op_day, uid,
                                   (profiles.get(uid) or {}).get('redemption_events') or [])
                source = ('GetBondEvents: выплата на одну облигацию'
                          if event_count is not None else 'подтверждённый номинал')
                warnings.add(
                    f'Погашение {op_day}, облигация …{uid[-8:]}: '
                    f'количество {count} шт. рассчитано по {source}. '
                    'Сверь с брокерским отчётом'
                )
        # Price only assets that can occur within the verified suffix.
        # Older, already-sold securities outside that suffix do not need candles.
        needed=set()
        for state in account_state.values():
            needed.update(uid for uid, qty in state['holdings'].items() if qty != 0)
            for op_day, operations in state['ops_by_day'].items():
                if op_day > cutoff:
                    needed.update(uid for kind, _, _, _, uid, _ in operations
                                  if uid and kind in (BUY_KINDS | SELL_KINDS | {REDEMPTION_FULL}))
        profiles={uid: profile for uid, profile in profiles.items() if uid in needed}
        from_day=cutoff-timedelta(days=35)
        end_day=anchor-timedelta(days=1)
        quotes={}
        for i,(uid, profile) in enumerate(profiles.items(),start=1):
            log.info('Загрузка исторических котировок: %d/%d, инструмент …%s',
                     i,len(profiles),uid[-8:])
            quotes[uid]={}
            if profile['kind']=='bond' and profile['nominal'] is None:
                # Missing face value: no guess in RUB. Keep gaps in history.
                continue
            sources = profile.get('quote_uids') or [uid]
            conflicting_dates = set()
            for quote_uid in sources:
                candles=self.api.daily_candles(quote_uid,from_day,end_day)
                for c in candles:
                    if not c.get('isComplete',True):
                        continue
                    close=quotation(c.get('close'))
                    if close<=0:
                        continue
                    qday=c['time'][:10]
                    quote_unit=str(c.get('priceCurrency') or 'RUB').strip().upper()
                    if profile['kind']=='bond':
                        if quote_unit not in ('RUB', 'PT.', 'PT'):
                            raise ValueError(
                                f'Котировка облигации {uid[-8:]} в единицах '
                                f'{quote_unit}: нужна отдельная конвертация'
                            )
                        px=close*profile['nominal']/Decimal(100)
                    else:
                        if quote_unit!='RUB':
                            raise ValueError(
                                f'Котировка {uid[-8:]} в валюте/единицах '
                                f'{quote_unit}: нужна конвертация'
                            )
                        px=close
                    if qday in conflicting_dates:
                        continue
                    if qday in quotes[uid]:
                        prior = quotes[uid][qday]
                        # Two venue prices can legitimately differ slightly.
                        # A large divergence likely signals incomparable
                        # quotations or a corporate event; don't use this day.
                        if abs(px-prior) / prior > Decimal('0.05'):
                            conflicting_dates.add(qday)
                            del quotes[uid][qday]
                            warnings.add(f'ETF {uid[-8:]}: цены разных площадок '
                                         f'сильно расходятся {qday}; день исключён')
                        continue
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
                    state['ops_by_day'].get(current, []), day=current, account_id=aid, profiles=profiles)
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
