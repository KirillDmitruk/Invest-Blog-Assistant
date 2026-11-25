"""Closed-trade daily results; entry and exit fees are recognized on close day."""
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo
import json

def daily_results(db):
    pending={};days={};tz=ZoneInfo('Europe/Moscow')
    for row in db.execute('SELECT uid,action,at,payload FROM lab_trades ORDER BY id'):
        uid,action,at,payload=row;detail=json.loads(payload)
        if action in ('open_long','open_short'):
            pending[uid]=Decimal(detail['fee']);continue
        if action!='close':continue
        date=datetime.fromisoformat(at.replace('Z','+00:00')).astimezone(tz).date().isoformat()
        day=days.setdefault(date,{'date':date,'net_pnl':Decimal(0),'closed':0,'wins':0,'losses':0,'flat':0,'unmatched':0})
        day['closed']+=1;entry_fee=pending.pop(uid,None)
        if entry_fee is None:day['unmatched']+=1;continue
        pnl=Decimal(detail['pnl_before_exit_fee'])-Decimal(detail['fee'])-entry_fee
        day['net_pnl']+=pnl;day['wins' if pnl>0 else 'losses' if pnl<0 else 'flat']+=1
    return [{**days[k],'net_pnl':None if days[k]['unmatched'] else format(days[k]['net_pnl'].quantize(Decimal('.01')),'f')} for k in sorted(days)]
