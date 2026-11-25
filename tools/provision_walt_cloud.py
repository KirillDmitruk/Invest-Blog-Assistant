"""Explicit one-time Walt cloud migration; no order endpoints or execution."""
import argparse,base64,json,os,secrets,sqlite3,subprocess,sys
from pathlib import Path
import requests
from cryptography.fernet import Fernet
from nacl.public import PublicKey,SealedBox
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from invest_blog.walt_agent import protect
from tools.export_walt_cloud import snapshot

ROOT=Path(r'C:\Projects\Invest-Blog-Assistant')
BUNDLE=Path(__file__).resolve().parents[1]/'data/walt-cloud-transfer.dpapi'
REPO='KirillDmitruk/Invest-Blog-Assistant'

def github():
    result=subprocess.run(['git','credential','fill'],input='protocol=https\nhost=github.com\n\n',capture_output=True,text=True,
        env=os.environ|{'GIT_TERMINAL_PROMPT':'0','GCM_INTERACTIVE':'Never'})
    values=dict(line.split('=',1) for line in result.stdout.splitlines() if '=' in line)
    if not values.get('password'):raise ValueError('GitHub authorization unavailable')
    session=requests.Session();session.headers.update({'Authorization':'Bearer '+values['password'],'Accept':'application/vnd.github+json'})
    return session

def api(session,method,path,expected=(200,),**kwargs):
    response=session.request(method,'https://api.github.com/repos/'+REPO+path,timeout=(5,40),**kwargs)
    if response.status_code not in expected:raise ValueError('GitHub operation not confirmed: '+str(response.status_code))
    return response.json() if response.content else {}

def bundle():return json.loads(protect(BUNDLE.read_bytes(),True))

def migrate(session):
    if BUNDLE.exists():keys=bundle()
    else:
        existing=api(session,'GET','/actions/secrets')['secrets']
        if any(v['name']=='WALT_CLOUD_STATE_KEY' for v in existing):raise ValueError('Existing cloud key must not be replaced')
        keys={'WALT_CLOUD_STATE_KEY':Fernet.generate_key().decode(),'WALT_EVENT_KEY':base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip('='),
            'WALT_BUTTON_KEY':secrets.token_urlsafe(48),'TG_WEBHOOK_SECRET':secrets.token_urlsafe(40)}
        BUNDLE.parent.mkdir(parents=True,exist_ok=True)
        with BUNDLE.open('xb') as handle:handle.write(protect(json.dumps(keys).encode()))
    envfile=ROOT/'.env';text=envfile.read_text(encoding='utf-8')
    # Stop concurrent token refresh before exporting the existing identity.
    import re
    for name in ('WALT_CLOUD_TRADING_ENABLED','WALT_TELEGRAM_CLOUD_ENABLED'):
        if re.search(r'^'+name+r'=',text,re.M):text=re.sub(r'^'+name+r'=.*$',name+'=true',text,flags=re.M)
        else:text=text.rstrip()+'\n'+name+'=true\n'
    envfile.write_text(text,encoding='utf-8')
    # Read just DB_PATH; never serialize .env into the exported state.
    match=re.search(r'^DB_PATH=(.*)$',text,re.M)
    path=Path(match.group(1).strip().strip('\"\'') if match else './data/drafts.sqlite3')
    if not path.is_absolute():path=ROOT/path
    encrypted=snapshot(path,keys['WALT_CLOUD_STATE_KEY'].encode())
    public=api(session,'GET','/actions/secrets/public-key');box=SealedBox(PublicKey(base64.b64decode(public['key'])))
    for name,value in keys.items():
        api(session,'PUT','/actions/secrets/'+name,expected=(201,204),json={
            'key_id':public['key_id'],'encrypted_value':base64.b64encode(box.encrypt(value.encode())).decode()})
    branch='automation/walt-private-state'
    check=session.get('https://api.github.com/repos/'+REPO+'/git/ref/heads/'+branch,timeout=20)
    if check.status_code==404:
        tree=api(session,'POST','/git/trees',expected=(201,),json={'tree':[{'path':'walt-state.enc','mode':'100644','type':'blob','content':encrypted.decode()}]})
        commit=api(session,'POST','/git/commits',expected=(201,),json={'message':'Initialize encrypted Walt-only cloud state','tree':tree['sha'],'parents':[]})
        api(session,'POST','/git/refs',expected=(201,),json={'ref':'refs/heads/'+branch,'sha':commit['sha']})
    elif check.status_code==200:
        # Existing initialized state wins. Never overwrite refreshed credentials.
        from tempfile import TemporaryDirectory
        from invest_blog.walt_cloud_state import CloudWaltState
        with TemporaryDirectory() as folder:
            store=CloudWaltState(Path(folder)/'state.db',REPO,session.headers['Authorization'][7:],keys['WALT_CLOUD_STATE_KEY'].encode())
            store.restore()
    else:raise ValueError('Cloud state branch not confirmed')
    print('Walt-only encrypted state and four service secrets saved. Local Walt/polling disabled. No orders sent.')

def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=('migrate','deploy-disabled','activate','enable','status'))
    parser.add_argument('--confirmed-transfer',action='store_true');args=parser.parse_args();session=github()
    if args.action=='migrate':
        if not args.confirmed_transfer:raise ValueError('Explicit transfer authorization required')
        migrate(session)
    elif args.action in ('deploy-disabled','activate'):
        api(session,'POST','/actions/workflows/deploy-walt-gateway.yml/dispatches',expected=(204,),json={'ref':'main','inputs':{'activate':args.action=='activate'}})
        print('Deployment requested:',args.action)
    elif args.action=='enable':
        check=session.get('https://api.github.com/repos/'+REPO+'/actions/variables/WALT_TELEGRAM_ENABLED',timeout=20)
        method='PATCH' if check.status_code==200 else 'POST';path='/actions/variables'+('/WALT_TELEGRAM_ENABLED' if method=='PATCH' else '')
        api(session,method,path,expected=(201,204),json={'name':'WALT_TELEGRAM_ENABLED','value':'true'})
        print('Cloud buttons enabled. No orders sent.')
    else:
        runs=api(session,'GET','/actions/workflows/deploy-walt-gateway.yml/runs',params={'per_page':3})['workflow_runs']
        print(json.dumps([{'id':r['id'],'status':r['status'],'conclusion':r['conclusion']} for r in runs]))

if __name__=='__main__':
    try:main()
    except Exception as exc:raise SystemExit('Cloud setup stopped: '+(str(exc) if type(exc) is ValueError else type(exc).__name__)+'. Secrets withheld; no order retry.') from None
