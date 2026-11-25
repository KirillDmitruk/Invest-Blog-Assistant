"""Restore display-only entry collateral from verified archived PAPER snapshots."""
import json,re
from decimal import Decimal,InvalidOperation


def positive(raw):
    try:
        n=Decimal(str(raw))
        return format(n,'f') if n.is_finite() and n>0 else None
    except (InvalidOperation,ValueError,TypeError):return None


def apply_entry_bases(db,bundle):
    if bundle.get('version')!=1 or bundle.get('kind')!='tinvest-futures-entry-bases' or bundle.get('budget')!='1000000' or not isinstance(bundle.get('entries'),dict):
        raise ValueError('Invalid archived entry bases')
    state=json.loads(db.execute('SELECT payload FROM lab_state WHERE id=1').fetchone()[0])
    if state.get('budget')!='1000000':raise ValueError('Wrong virtual account')
    pending={};restored=0
    for row in db.execute('SELECT id,uid,at,action,payload FROM lab_trades ORDER BY id').fetchall():
        ident,uid,at,action,raw=row;detail=json.loads(raw);before=dict(detail)
        if action in ('open_long','open_short'):
            archive=bundle['entries'].get(str(ident),{})
            same=archive.get('uid')==uid and archive.get('at')==at and archive.get('action')==action
            same=same and all(archive.get(k)==detail.get(k) for k in ('price','contracts','side'))
            source=archive.get('source_commit','')
            if same and re.fullmatch('[0-9a-f]{40}',source) and positive(archive.get('entry_margin')) and detail.get('entry_margin') is None:
                detail['entry_margin']=positive(archive['entry_margin']);detail['entry_margin_source']=source
            pending[uid]=detail
        elif action=='close':
            entry=pending.pop(uid,None)
            if entry and positive(entry.get('entry_margin')):
                if detail.get('entry_margin') is None:
                    detail['entry_margin']=entry['entry_margin']
                    if entry.get('entry_margin_source'):detail['entry_margin_source']=entry['entry_margin_source']
                if detail.get('entry_fee') is None and entry.get('fee') is not None:detail['entry_fee']=entry['fee']
        if detail!=before:
            db.execute('UPDATE lab_trades SET payload=? WHERE id=?',(json.dumps(detail,ensure_ascii=False),ident));restored+=1
    return restored
