"""Read-only minute replay for recorded futures entries; no portfolio/order API calls."""
import argparse,json,sys,math
from pathlib import Path
from datetime import datetime,timedelta
from dotenv import dotenv_values
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from invest_blog.trading_market import FuturesMarket
from invest_blog.model import quotation
from invest_blog.profit_protection_study import compare_exits

def main():
    p=argparse.ArgumentParser();p.add_argument('--overview',type=Path,required=True);p.add_argument('--env-file',type=Path,required=True);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    raw=json.loads(args.overview.read_text(encoding='utf-8'));state=raw['state'];cfg=dotenv_values(args.env_file)
    market=FuturesMarket(cfg.get('TINVEST_TOKEN',''),None);market.session.verify=str(args.env_file.parent/'deploy/tbank-ca.pem')
    pending={};cases=[];baseline=datetime.fromisoformat(state['strategy_baseline']['at'])
    for row in sorted(raw['trades'],key=lambda t:t['id']):
        if row['action'].startswith('open'):pending[row['uid']]=row;continue
        if row['action']!='close':continue
        op=pending.pop(row['uid'],None)
        if not op or datetime.fromisoformat(op['at'])<baseline:continue
        result={'symbol':row['ticker'],'open_at':op['at'],'close_at':row['at']}
        try:
            c=state['watchlist'][row['uid']];d=op['detail'];direction=1 if d['side']=='long' else -1
            entry=float(d['price']);tick=float(c['tick']);slip=int(state['risk']['slippage_ticks'])*tick
            stop=entry-direction*int(d['stop_ticks'])*tick;target=entry+direction*int(d['stop_ticks'])*tick*2
            start=datetime.fromisoformat(op['at']).replace(second=0,microsecond=0)+timedelta(minutes=1)
            end=datetime.fromisoformat(row['at']).replace(second=0,microsecond=0)+timedelta(minutes=1);bars={}
            cursor=start
            while cursor<end:
                until=min(cursor+timedelta(hours=6),end)
                items=market.read('MarketDataService','GetCandles',{'instrumentId':row['uid'],'from':cursor.isoformat(),'to':until.isoformat(),'interval':'CANDLE_INTERVAL_1_MIN','limit':1000}).get('candles',[])
                for b in items:
                    t=datetime.fromisoformat(b['time'].replace('Z','+00:00'))
                    if b.get('isComplete') and start<=t<end:bars[int(t.timestamp())]={'t':int(t.timestamp()),**{a:float(quotation(b[z])) for a,z in [('o','open'),('h','high'),('l','low'),('c','close')]}}
                cursor=until
            rows=[bars[t] for t in sorted(bars)];expected=list(range(int(start.timestamp()),int(end.timestamp()),60))
            if [b['t'] for b in rows]!=expected:raise ValueError('Minute gaps or closed session: no continuous replay claim')
            cost=2*float(state['risk']['commission'])/float(c['tick_value'])*tick
            replay=compare_exits(rows,entry,stop,target,d['side'],slip,cost)
            actual=float(row['detail']['price']);old=replay['baseline'];new=replay['break_even']
            if not old or abs(old['exit']-actual)>tick*.01:raise ValueError('Baseline exit does not match recorded fill')
            if not new:raise ValueError('Alternative exit not observed')
            risk=abs(entry-stop)
            result.update(status='matched',actual_r=(actual-entry)*direction/risk,alternative_r=(new['exit']-entry)*direction/risk,alternative_reason=new['reason'],delta_r=(new['exit']-actual)*direction/risk)
        except Exception as e:result.update(status='unverified',reason=str(e)[:180])
        cases.append(result);print(json.dumps(result,ensure_ascii=True),flush=True)
    matched=[c for c in cases if c['status']=='matched'];out={'scope':'Fixed recorded entries, continuous closed minute bars, conservative next-minute break-even at +1R. R is price return, not account return. Fees approximated in break-even level; current tick value, no entry resimulation or future-path extrapolation. Not an out-of-sample strategy backtest.','cases':cases,'matched':len(matched),'delta_r':sum(c['delta_r'] for c in matched),'enabled':False}
    args.output.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'matched':len(matched),'total':len(cases),'delta_r':out['delta_r'],'enabled':False}))
if __name__=='__main__':main()
