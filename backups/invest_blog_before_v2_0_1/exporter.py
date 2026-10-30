"""CSV analytics export for locally hosted portfolio dashboard.

No tokens, account IDs, operation JSON, or inferred historical values are exported.
"""
from __future__ import annotations

import csv
import io
import json
import zipfile
from datetime import datetime, timezone


def csv_bytes(headers: list[str], rows: list[list[object]]) -> bytes:
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow(headers)
    for row in rows:
        if len(row) != len(headers):
            raise ValueError('CSV column mismatch')
        # Spreadsheet injection protection if textual fields are imported into Excel.
        writer.writerow(["'"+v if isinstance(v,str) and v.lstrip().startswith(('=', '+','-','@','\t','\r')) else v
                         for v in row])
    return output.getvalue().encode('utf-8-sig')


def export_tables(report: dict) -> dict[str, bytes]:
    period = report.get('period') or {}
    performance = csv_bytes(
        ['Период','Начало','Конец','Статус','TWR/Modified Dietz %','TWR точная','XIRR годовая %',
         'Начальная стоимость ₽','Конечная стоимость ₽','Пополнения минус выводы ₽','Результат ₽','Причина/ограничения'],
        [[item.get('label','Выбранный'),item.get('from'),item.get('to'),item.get('status'),
          item.get('twr_pct'),item.get('exact_twr'),item.get('xirr_pct'),item.get('start_value_rub'),
          item.get('end_value_rub'),item.get('net_contributions_rub'),item.get('profit_rub'),
          item.get('reason','') or '; '.join(item.get('warnings',[]))]
         for item in [period]+report.get('comparisons',[]) if item])
    history = csv_bytes(['Дата','Стоимость ₽','Источник'],
                        [[r['date'],r['value_rub'],r['source']] for r in report.get('history',[])])
    cashflows = csv_bytes(['Дата','Внешний денежный поток ₽','Тип'],
                          [[r['date'],r['amount_rub'],r['type']] for r in report.get('cashflows',[])])
    return {'performance.csv': performance, 'broker_snapshots.csv':history, 'external_cashflows.csv':cashflows}


def export_all_tables(store, report: dict) -> dict[str, bytes]:
    """Return read-only analytics tables without account IDs or API tokens."""
    tables = export_tables(report)
    with store.connect() as db:
        latest = db.execute('SELECT MAX(day) FROM portfolio_snapshots').fetchone()[0]
        positions = db.execute("""SELECT day,kind,name,ticker,quantity,price,currency,nkd,maturity_date
            FROM position_snapshots WHERE day=? ORDER BY kind,name""", (latest,)).fetchall() if latest else []
        incomes = db.execute("""SELECT substr(executed_at,1,7) AS month,kind,currency,
           SUM(CAST(amount AS REAL)) AS amount,COUNT(*) AS n FROM broker_operations
           WHERE kind IN ('OPERATION_TYPE_COUPON','OPERATION_TYPE_DIVIDEND','OPERATION_TYPE_DIV_EXT')
           GROUP BY month,kind,currency ORDER BY month,kind,currency""").fetchall()
    tables['current_positions.csv'] = csv_bytes(
        ['Снимок','Тип','Название','Тикер','Количество','Цена в единицах котировки','Валюта/единица','НКД','Погашение'],
        [[r['day'],r['kind'],r['name'],r['ticker'],r['quantity'],r['price'],
          r['currency'],r['nkd'],r['maturity_date']] for r in positions])
    tables['income_by_month.csv'] = csv_bytes(
        ['Месяц','Тип дохода','Валюта','Сумма в валюте','Операций'],
        [[r['month'],r['kind'],r['currency'],round(r['amount'],2),r['n']] for r in incomes])
    return tables


def export_zip(report: dict, store=None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as z:
        for name,content in (export_all_tables(store, report) if store is not None else export_tables(report)).items():
            z.writestr(name,content)
        z.writestr('methodology.txt', '\n'.join(report.get('notes',[])).encode('utf-8'))
        z.writestr('export_info.json', json.dumps({
            'generated_at':datetime.now(timezone.utc).isoformat(),
            'version':'1.5.0', 'status':report.get('status'),
            'period':report.get('period'),
        },ensure_ascii=False,indent=2,default=str))
    return buffer.getvalue()
