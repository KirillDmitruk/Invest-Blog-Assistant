"""Deploy a free Cron Trigger for the two virtual account workflows only."""
import json,os,re
from pathlib import Path
import requests

def main():
    if os.environ.get('TRADING_MODE')!='paper':raise ValueError('PAPER mode required')
    account=os.environ['CF_ACCOUNT_ID'];repo=os.environ['GITHUB_REPOSITORY']
    if not re.fullmatch('[a-f0-9]{32}',account):raise ValueError('Invalid account')
    base='https://api.cloudflare.com/client/v4/accounts/'+account+'/workers/scripts/content-hub-demo-scheduler'
    headers={'Authorization':'Bearer '+os.environ['CF_WORKERS_DEPLOY_TOKEN']};session=requests.Session()
    def call(method,url,**kw):
        response=session.request(method,url,headers=headers,timeout=(5,30),**kw)
        if response.status_code>=300:raise ValueError('Cloudflare scheduler HTTP '+str(response.status_code))
        value=response.json()
        if value.get('success') is not True:raise ValueError('Scheduler operation not confirmed')
        return value
    metadata={'main_module':'worker.mjs','compatibility_date':'2026-11-23','bindings':[
        {'type':'secret_text','name':'GH_DISPATCH_TOKEN','text':os.environ['WALT_GH_DISPATCH_TOKEN']},
        {'type':'plain_text','name':'GITHUB_REPOSITORY','text':repo},
        {'type':'plain_text','name':'TRADING_MODE','text':'paper'},
        {'type':'plain_text','name':'DEMO_MONITOR_ENABLED','text':'true'},
        {'type':'plain_text','name':'CLOUD_NEWS_ENABLED','text':os.environ.get('CLOUD_NEWS_ENABLED','false')}],
        'observability':{'enabled':False},'logpush':False}
    code=(Path(__file__).resolve().parents[1]/'cloudflare/demo-scheduler/worker.mjs').read_bytes()
    call('PUT',base,files={'metadata':('metadata.json',json.dumps(metadata),'application/json'),
        'worker.mjs':('worker.mjs',code,'application/javascript+module')})
    call('PUT',base+'/schedules',json=[{'cron':'*/5 * * * *'}])
    result=call('GET',base+'/schedules')['result']
    schedules=result if isinstance(result,list) else result.get('schedules',[])
    if not any(x.get('cron')=='*/5 * * * *' for x in schedules):raise ValueError('Five-minute schedule not verified')
    print('Independent Cloudflare PAPER monitor deployed. Cron verified: every 5 minutes. No broker keys or real workflows.')

if __name__=='__main__':
    try:main()
    except Exception as exc:raise SystemExit('Scheduler deploy stopped: '+(str(exc) if type(exc) is ValueError else type(exc).__name__)+'. Secrets withheld.') from None
