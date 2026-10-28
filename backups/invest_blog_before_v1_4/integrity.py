"""Independent integrity checks for persisted broker data (no hidden fixes)."""
from __future__ import annotations

from .portfolio_store import PortfolioStore


def check_integrity(store: PortfolioStore) -> dict:
    with store.connect() as db:
        suspicious = [dict(r) for r in db.execute('''
            SELECT substr(executed_at,1,19) AS moment, kind, currency, amount,
                   COALESCE(instrument_id,'') AS instrument_id, COUNT(*) AS possible_copies
            FROM broker_operations
            GROUP BY account_id,moment,kind,currency,amount,instrument_id
            HAVING COUNT(*)>1 ORDER BY possible_copies DESC LIMIT 15''')]
        operations = db.execute('SELECT COUNT(*) FROM broker_operations').fetchone()[0]
        snapshots = db.execute('SELECT COUNT(DISTINCT day) FROM portfolio_snapshots').fetchone()[0]
        bad_states = db.execute('''SELECT COUNT(*) FROM broker_operations
           WHERE json_extract(raw_json,'$.state') NOT IN ('OPERATION_STATE_EXECUTED',1)
           AND json_extract(raw_json,'$.state') IS NOT NULL''').fetchone()[0]
        missing_currency = db.execute('''SELECT COUNT(*) FROM broker_operations
           WHERE currency='' OR currency IS NULL''').fetchone()[0]
    issues=[]
    if suspicious:
        issues.append(f'Есть {len(suspicious)} групп потенциальных дублей. Не удаляй их автоматически: сделки могут совпадать по времени и сумме.')
    if bad_states:issues.append(f'Найдены {bad_states} операции с состоянием, отличным от EXECUTED.')
    if missing_currency:issues.append(f'Найдены {missing_currency} операции без валюты.')
    return {'operations': operations, 'snapshot_days':snapshots,
            'possible_duplicates':suspicious, 'bad_states':bad_states,
            'missing_currency':missing_currency,'issues':issues,
            'broker_report_reconciled':False}
