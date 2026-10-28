"""Read-only sync; raises on partial broker reads rather than silently saving zeros."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from .model import iso_day, iso_dt, money, quotation
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
            records=self.api.operations(aid, day-timedelta(days=days-1), day)
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
        self.store.save_batch(day,accounts,operations,notes)
        return {'day':day.isoformat(),'accounts':len(accounts), 'positions':sum(len(a['positions']) for a in accounts),
                'operations':len(operations), 'warnings':notes}
