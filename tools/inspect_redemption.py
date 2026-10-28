"""Read-only T-Invest redemption diagnostics; prints no API tokens/account IDs."""
import argparse
import json
from datetime import date, timedelta

from invest_blog.config import Settings
from invest_blog.portfolio_store import PortfolioStore
from invest_blog.tinvest import TInvest, TInvestError
from invest_blog.model import money


def main():
    parser=argparse.ArgumentParser(description='Информация о погашении облигации')
    parser.add_argument('--suffix', required=True, help='Последние символы UID или FIGI')
    parser.add_argument('--day', required=True, help='Дата операции YYYY-MM-DD')
    args=parser.parse_args()
    day=date.fromisoformat(args.day)
    settings=Settings.load()
    store=PortfolioStore(settings.db_path)
    ops=[]
    with store.connect() as db:
        rows=db.execute('''SELECT account_id,instrument_id,amount,currency,executed_at,raw_json
            FROM broker_operations WHERE kind='OPERATION_TYPE_BOND_REPAYMENT_FULL'
            ORDER BY executed_at''').fetchall()
        for row in rows:
            if not str(row['instrument_id'] or '').endswith(args.suffix):
                continue
            raw=json.loads(row['raw_json'])
            ts=str(row['executed_at'])
            # A ±1 day check handles storage timestamps crossing UTC boundaries.
            if abs((date.fromisoformat(ts[:10])-day).days)>1:
                continue
            ops.append(row)
        if not ops:
            print('Подходящие операции погашения в SQLite не найдены.')
            return
        for row in ops:
            aid,uid=row['account_id'],row['instrument_id']
            op=json.loads(row['raw_json'])
            print(f'Счёт …{aid[-4:]}, облигация …{uid[-8:]}, операция {row["executed_at"]}')
            print(f'  Поступление: {row["amount"]} {row["currency"]}; '
                  f'quantityDone={op.get("quantityDone")!r}; quantity={op.get("quantity")!r}')
            near=db.execute('''SELECT day,quantity,name FROM position_snapshots
                WHERE account_id=? AND instrument_id=? AND day<=?
                ORDER BY day DESC LIMIT 2''',(aid,uid,day.isoformat())).fetchall()
            for p in near:
                print(f'  Снимок: {p["day"]}, {p["name"]}, {p["quantity"]} шт.')
            cached=db.execute('SELECT raw_json FROM bond_metadata WHERE instrument_id=?',(uid,)).fetchone()
            if cached:
                b=json.loads(cached['raw_json'])
                print('  Кэш облигации:',{
                    'ticker':b.get('ticker'),'isin':b.get('isin'),
                    'nominal':b.get('nominal'),'initialNominal':b.get('initialNominal'),
                    'amortizationFlag':b.get('amortizationFlag')})
            else:
                print('  Метаданных облигации в кэше нет')
    settings.validate_api()
    api=TInvest(settings.invest_token,settings.timezone)
    for uid in sorted({r['instrument_id'] for r in ops}):
        try:
            info=api.instrument(uid=uid)
            canonical=info.uid or uid
            print(f'Инструмент: {info.name}, {info.ticker}, {info.isin}, UID …{canonical[-8:]}')
        except TInvestError as exc:
            canonical=uid
            print(f'GetInstrumentBy недоступен: {exc.__class__.__name__}; пробуем исходный ID')
        try:
            b=api.bond(canonical)
            print('BondBy:',{
                'nominal':b.get('nominal'), 'initialNominal':b.get('initialNominal'),
                'amortizationFlag':b.get('amortizationFlag'),
                'maturityDate':b.get('maturityDate')})
        except TInvestError as exc:
            print(f'BondBy недоступен: {exc.__class__.__name__}')
        try:
            events=api.bond_events(canonical,day-timedelta(days=14),day+timedelta(days=14))
            print(f'GetBondEvents: {len(events)} событий')
            for e in events[:12]:
                print('  ',{'eventType':e.get('eventType'), 'eventDate':e.get('eventDate'),
                    'payDate':e.get('payDate'), 'realPayDate':e.get('realPayDate'),
                    'payOneBond':e.get('payOneBond'), 'operationType':e.get('operationType'),
                    'execution':e.get('execution')})
        except TInvestError as exc:
            print(f'GetBondEvents недоступен: {exc.__class__.__name__}')
    print('Никогда не публикуй полный токен, ID счёта или полный JSON запроса.')


if __name__=='__main__':
    main()
