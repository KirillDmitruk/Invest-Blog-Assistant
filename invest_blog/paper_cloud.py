"""Cloud PAPER execution using only public HTTP GET market data and encrypted JSON.

No Walt/T-Invest OAuth, signer, order tools, or real-order imports are used.
"""
from contextlib import closing
from datetime import datetime,timezone
from decimal import Decimal
from pathlib import Path
import base64,json,os,re,time
import requests
from cryptography.fernet import Fernet
from .cloud_signals import json_call,previous_public_report
from .paper_perps import PaperEngine,initial,num,amount

BASE='https://mainnet.zklighter.elliot.ai/api/v1/'

class PublicPaperMarket:
    def __init__(self,session=None):self.session=session or requests.Session();self.last=0
    def get(self,path,params):
        if path not in ('orderBookDetails','markPriceCandles','fundings'):raise ValueError('Only public price/history reads allowed')
        delay=1.3-(time.monotonic()-self.last)
        if delay>0:time.sleep(delay)
        self.last=time.monotonic();value=json_call(self.session,'GET',BASE+path,params=params)
        if value.get('code')!=200:raise ValueError('Public source unavailable')
        return value
    def quote(self,ident,now):
        rows=self.get('orderBookDetails',{'market_id':ident})['order_book_details']
        rows=[r for r in rows if r.get('market_id')==ident and r.get('market_type')=='perp']
        if len(rows)!=1:raise ValueError('Ambiguous public contract')
        r=rows[0];im=num(r['min_initial_margin_fraction']);mm=num(r['maintenance_margin_fraction'])
        if not 0<mm<=im<10000 or num(r.get('multiplier',1))!=1:raise ValueError('Unsupported public contract')
        return {'symbol':r['symbol'],'price':amount(r['mark_price']),'at':time.time(),
            'active':r.get('status')=='active' and r.get('is_frozen') is False and not r.get('market_config',{}).get('force_reduce_only',False),
            'max_leverage':int(Decimal(10000)/im),'step':amount(Decimal(10)**-int(r['supported_size_decimals'])),
            'min_quantity':r['min_base_amount'],'min_notional':r['min_quote_amount'],'maintenance':amount(mm/10000)}
    def bars(self,ident,since,now):
        start=int(since//60*60);end=int(now//60*60)
        if end-start>8*3600:raise ValueError('Monitoring history exceeds eight-hour recovery window')
        if end<=start:return []
        raw=self.get('markPriceCandles',{'market_id':ident,'resolution':'1m','start_timestamp':start,
            'end_timestamp':end-1,'count_back':min(500,(end-start)//60+1),'set_timestamp_to_end':'false'})
        out=[];seen=set()
        for b in raw['c']:
            t=int(b['t'])//1000
            if not start<=t<end:continue
            if t%60 or t in seen:raise ValueError('Ambiguous minute history')
            seen.add(t);out.append({'t':t,**{k:amount(b[k]) for k in ('o','h','l','c')}})
        return sorted(out,key=lambda b:b['t'])
    def observe(self,ident,since,now):
        q=self.quote(ident,now);q['bars']=self.bars(ident,since,now)
        # Settled funding `value`: quote amount per unit of base, payer is direction.
        # This is a simulation model, not a replication of Wallet fees/subscription.
        start=int(since//3600*3600)
        items=self.get('fundings',{'market_id':ident,'resolution':'1h','start_timestamp':start,
            'end_timestamp':int(now),'count_back':12}).get('fundings',[])
        funding=[];seen=set()
        for f in items:
            t=int(f['timestamp']);value=num(f['value'])
            if t in seen or f['direction'] not in ('long','short') or value<0 or value>num(q['price'])*Decimal('.1'):raise ValueError('Invalid funding history')
            seen.add(t)
            if start<=t<=now:funding.append({'t':t,'value':amount(value),'direction':f['direction']})
        # Missing hourly records must not silently become zero funding.
        expected=set(range(int(since//3600+1)*3600,int(now//3600)*3600+1,3600))
        if expected-set(f['t'] for f in funding):raise ValueError('Incomplete settled funding history')
        q['fundings']=funding;return q
    def entry(self,ident,now):
        q=self.quote(ident,now);bars=self.bars(ident,now-180,now)
        if not bars or bars[-1]['t']+60<now-120 or not any(num(b['h'])>num(b['l']) for b in bars):raise ValueError('No fresh moving minute data for entry')
        items=self.get('fundings',{'market_id':ident,'resolution':'1h','start_timestamp':int(now)-7200,'end_timestamp':int(now),'count_back':2}).get('fundings',[])
        if not items:raise ValueError('No recent settled funding')
        f=max(items,key=lambda f:int(f['timestamp']))
        if not 0<=now-int(f['timestamp'])<=7200 or f['direction'] not in ('long','short') or not 0<=num(f['value'])<=num(q['price'])*Decimal('.1'):raise ValueError('Invalid entry funding')
        q.update(hour_funding_value=amount(f['value']),funding_payer=f['direction'])
        return q

class PaperStore:
    branch='automation/paper-perps-state';filename='paper-state.enc'
    def __init__(self,repo,token,key,session=None):
        if not re.fullmatch(r'[\w.-]+/[\w.-]+',repo):raise ValueError('Invalid repository')
        self.base='https://api.github.com/repos/'+repo;self.headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json'}
        self.session=session or requests.Session();self.cipher=Fernet(key);self.sha=None
    def load(self):
        meta=json_call(self.session,'GET',self.base+'/contents/'+self.filename,headers=self.headers,params={'ref':self.branch})
        self.sha=meta['sha']
        if not meta.get('content'):meta=json_call(self.session,'GET',self.base+'/git/blobs/'+self.sha,headers=self.headers)
        state=json.loads(self.cipher.decrypt(base64.b64decode(meta['content'])))
        PaperEngine(state) # No silent empty account on missing/corrupt state.
        return state
    def save(self,state):
        if not self.sha:raise ValueError('State not loaded')
        raw=json.dumps(state,ensure_ascii=False,separators=(',',':')).encode()
        if len(raw)>1_000_000:raise ValueError('Paper state size limit')
        meta=json_call(self.session,'PUT',self.base+'/contents/'+self.filename,headers=self.headers,json={
            'branch':self.branch,'sha':self.sha,'message':'Checkpoint encrypted virtual account',
            'content':base64.b64encode(self.cipher.encrypt(raw)).decode()})
        self.sha=meta['content']['sha']

def latest_report(now):
    repo=os.environ['GITHUB_REPOSITORY'];headers={'Authorization':'Bearer '+os.environ['GH_STATE_TOKEN']}
    data=json_call(requests.Session(),'GET','https://api.github.com/repos/'+repo+'/actions/workflows/cloud-signals.yml/runs',
        headers=headers,params={'status':'success','per_page':10})
    for run in data['workflow_runs']:
        try:
            p=previous_public_report(str(run['id']));stamp=datetime.fromisoformat(p['created_at']).timestamp()
            if 0<=now-stamp<=10800:p['created_epoch']=stamp;return p
        except ValueError:continue
    return None

def notification(engine):
    from .model import fmt_money
    s=engine.state;usd=lambda x:fmt_money(num(x),'USD')
    lines=['🧪 Виртуальная торговля · PAPER',f'Капитал: {usd(s["equity"])} · старт $10 000',
        f'Открытых позиций: {len(s["positions"])} · результат: {usd(num(s["equity"])-10000)}']
    reasons={'stop':'стоп-лосс','stop_quote':'стоп по котировке','take':'тейк-профит','take_quote':'тейк по котировке','liquidation_model':'ликвидация в модели'}
    for e in engine.changes:
        if e['kind']=='open':lines.append(f'🟦 Открыта виртуально: {e["symbol"]} · {"лонг" if e["side"]=="long" else "шорт"} · маржа {usd(e["margin"])}')
        else:lines.append(f'▫️ Закрыта виртуально: {e["symbol"]} · {reasons.get(e["reason"],e["reason"])} · {usd(e["pnl"])}')
    if s.get('warnings') or s.get('paused') or not engine.changes:lines+=['',s['reason'],*s.get('warnings',[])[:2]]
    lines+=['','Реальных заявок нет. Исполнение, комиссии и фандинг моделируются. Позиции и журнал — в хабе → «Автотрейдинг · демо».']
    return '\n'.join(lines)

def main():
    if os.getenv('TRADING_MODE')!='paper' or os.getenv('PAPER_ENABLED')!='true':raise SystemExit('Paper mode is not enabled.')
    try:
        now=time.time();store=PaperStore(os.environ['GITHUB_REPOSITORY'],os.environ['GH_STATE_TOKEN'],os.environ['PAPER_STATE_KEY'].encode())
        previous=store.load()
        if previous.get('last_cycle') and 0<=now-float(previous['last_cycle'])<240:
            print('Recent virtual crypto cycle already saved; duplicate trigger skipped.');return
        engine=PaperEngine(previous);packet=latest_report(now)
        engine.cycle(packet,PublicPaperMarket(),now)
        notify=bool(engine.changes) or engine.state.get('warnings',[])!=previous.get('warnings',[]) or engine.state.get('paused')!=previous.get('paused');tag=str(int(now))
        if notify:engine.state['notifications'][tag]='attempted';engine.state['notifications']=dict(list(engine.state['notifications'].items())[-100:])
        store.save(engine.state) # Persist all virtual actions and send intent before Telegram.
        print('Paper cycle saved. Virtual positions:',len(engine.state['positions']),'changes:',len(engine.changes))
        if notify:
            bot=os.environ['SIGNALS_BOT_TOKEN'];chat=os.environ['SIGNALS_CHAT_ID']
            session=requests.Session();url='https://api.telegram.org/bot'+bot+'/'
            me=json_call(session,'POST',url+'getMe',json={});target=json_call(session,'POST',url+'getChat',json={'chat_id':chat})
            if not me.get('ok') or not target.get('ok') or target['result'].get('type')!='private' or str(target['result']['id'])!=chat or str(me['result']['id'])==chat:raise ValueError('Private recipient not confirmed')
            text=notification(engine)
            value=json_call(session,'POST',url+'sendMessage',json={'chat_id':chat,'text':text})
            if not value.get('ok'):raise ValueError('Notification not confirmed; no send retry')
            engine.state['notifications'][tag]='sent';store.save(engine.state)
        print('No broker credentials or real-order calls used.')
    except Exception:raise SystemExit('Paper cycle not confirmed. Details withheld. No real-order path exists.') from None

if __name__=='__main__':main()
