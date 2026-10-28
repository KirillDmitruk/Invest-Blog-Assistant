"""Read-only sync; raises on partial broker reads rather than silently saving zeros."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from .model import iso_day, iso_dt, money, quotation
from .bond_analytics import add_months, month_floor
from .portfolio_store import PortfolioStore
from .posts import COUPON, DIVIDEND, REPAYMENT, DEPOSIT, BUY, operation_type
from .tinvest import TInvest, TInvestError


class PortfolioSync:
    def __init__(self, api: TInvest, accounts: tuple[str, ...], store: PortfolioStore):
        if not accounts:
            raise ValueError('Не указаны счета')
        self.api, self.accounts, self.store = api, accounts, store

    def run(self, day: date, days: int = 90) -> dict:
        if days < 1 or days > 3650:
            raise ValueError('days должен быть от 1 до 3650')
        notes=[]
        accounts=[]
        operations=[]
        bond_identifiers: dict[str, str | None] = {}
        first_day=day-timedelta(days=days-1)
        for aid in self.accounts:
            raw = self.api.portfolio(aid)
            if not isinstance(raw.get('positions'), list) or not isinstance(raw.get('totalAmountPortfolio'), dict):
                raise TInvestError('GetPortfolio: не хватает totalAmountPortfolio/positions')
            positions=[]
            for pos in raw['positions']:
                uid=pos.get('instrumentUid') or pos.get('figi') or pos.get('positionUid')
                if not uid:
                    raise TInvestError('Позиция без instrumentUid/figi/positionUid')
                qty=quotation(pos.get('quantity'))
                if qty == 0:
                    continue
                kind=pos.get('instrumentType', 'unknown')
                if kind.lower() in ('bond', 'instrument_type_bond'):
                    bond_identifiers[uid] = pos.get('instrumentUid') or None
                detail=self.store.existing_detail(uid)
                if detail:
                    name,ticker,maturity=detail['name'],detail['ticker'],detail['maturity_date']
                else:
                    name=pos.get('name') or uid
                    ticker=pos.get('ticker') or ''
                    maturity=None
                    valid_details=True
                    try:
                        info=self.api.instrument(uid=pos.get('instrumentUid') or '',figi=pos.get('figi') or '')
                        name,ticker=info.name,info.ticker
                        if kind=='bond':
                            bond=self.api.bond(info.uid)
                            maturity_day=iso_day(bond.get('maturityDate'), self.api.tz)
                            maturity=maturity_day.isoformat() if maturity_day else None
                    except (TInvestError, ValueError, KeyError) as exc:
                        notes.append(f'Недоступны характеристики {uid[-8:]}: {type(exc).__name__}')
                        valid_details=False
                    if valid_details:
                        self.store.save_detail(uid,name,ticker,maturity)
                price_obj=pos.get('currentPrice')
                price,currency=money(price_obj)
                nkd,_=money(pos.get('currentNkd'))
                if price_obj is None:
                    notes.append(f'Нет котировки для {uid[-8:]}')
                positions.append({'id':uid,'name':name,'ticker':ticker,'kind':kind,
                    'quantity':str(qty),'price':str(price) if price_obj is not None else None,
                    'currency':currency if price_obj is not None else None,
                    'nkd':str(nkd) if pos.get('currentNkd') is not None else None,
                    'maturity':maturity})
            accounts.append({'account_id':aid, 'portfolio':raw, 'positions':positions})
            records=self.api.operations(aid, first_day, day)
            for record in records:
                if operation_type(record) in (COUPON | DIVIDEND | REPAYMENT | DEPOSIT | BUY):
                    payment = record.get('payment')
                    if not isinstance(payment, dict) or not payment.get('currency'):
                        raise TInvestError('Операция без payment/currency; отказ от фиктивных нулей')
                if not record.get('id') and not record.get('cursor'):
                    raise TInvestError('Операция без ключа, sync прерван без сохранения')
                # Month-boundary aggregation must use the configured broker timezone.
                localized = dict(record)
                localized['date'] = iso_dt(record['date']).astimezone(self.api.tz).isoformat()
                operations.append((aid,localized))
        profiles: dict[str, dict] = {}
        schedules: dict[str, list[dict]] = {}
        # Isolate per-instrument failures: a missing coupon schedule is explicitly
        # incomplete and must never be displayed as a confirmed zero.
        if hasattr(self.api, 'coupons') and hasattr(self.api, 'bond'):
            to_exclusive=add_months(month_floor(day),12)
            horizon_end=to_exclusive-timedelta(days=1)
            for stored_id, instrument_uid in bond_identifiers.items():
                metadata_fresh, coupons_fresh=self.store.bond_cache(stored_id, day)
                if not metadata_fresh:
                    if not instrument_uid:
                        notes.append(f'Нет instrumentUid для метаданных облигации {stored_id[-8:]}')
                    else:
                        try:
                            profile=self.api.bond(instrument_uid)
                            if not isinstance(profile,dict) or not profile:
                                raise TInvestError('BondBy вернул пустую карточку')
                            profiles[stored_id]=profile
                        except (TInvestError, ValueError, KeyError, TypeError) as exc:
                            notes.append(f'BondBy {stored_id[-8:]}: {type(exc).__name__}')
                if not coupons_fresh:
                    try:
                        events=self.api.coupons(instrument_uid or stored_id,day,horizon_end)
                        if not isinstance(events,list):
                            raise TInvestError('GetBondCoupons: нет списка events')
                        try:
                            for event in events:
                                if not isinstance(event,dict) or 'couponNumber' not in event:
                                    raise ValueError('couponNumber missing')
                                date.fromisoformat(event['couponDate'][:10])
                                int(event['couponNumber'])
                        except (KeyError, ValueError, TypeError) as invalid:
                            raise TInvestError('GetBondCoupons: неполное событие купона') from invalid
                        schedules[stored_id]=events
                    except (TInvestError, ValueError, KeyError, TypeError) as exc:
                        notes.append(f'GetBondCoupons {stored_id[-8:]}: {type(exc).__name__}')
        self.store.save_batch(day,accounts,operations,notes,
                              bond_metadata=profiles,coupon_schedules=schedules,
                              coverage_ranges=[(aid,first_day,day) for aid in self.accounts])
        return {'day':day.isoformat(),'accounts':len(accounts), 'positions':sum(len(a['positions']) for a in accounts),
                'operations':len(operations), 'bond_profiles_updated':len(profiles),
                'coupon_calendars_updated':len(schedules), 'warnings':notes}
