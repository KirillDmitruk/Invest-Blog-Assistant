"""Provision a new isolated virtual RUB account; never exports the hub database."""
import base64,json,re,sys,tempfile
from pathlib import Path
from cryptography.fernet import Fernet
from nacl.public import PublicKey,SealedBox
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.provision_walt_cloud import github,api,ROOT,REPO
from invest_blog.walt_agent import protect
from invest_blog.tinvest_paper_cloud import seed,pack,FuturesStore
from invest_blog.trading_lab import TradingLab
from dotenv import dotenv_values

def main():
    s=github();keyfile=ROOT/'data/tinvest-paper-key.dpapi'
    secrets=api(s,'GET','/actions/secrets')['secrets']
    if not any(x['name']=='PULSE_TINVEST_TOKEN' for x in secrets):raise ValueError('Previously approved read-only broker secret missing')
    if keyfile.exists():key=protect(keyfile.read_bytes(),True)
    else:
        if any(x['name']=='TINVEST_PAPER_STATE_KEY' for x in secrets):raise ValueError('Existing key cannot be replaced')
        key=Fernet.generate_key();keyfile.parent.mkdir(parents=True,exist_ok=True)
        with keyfile.open('xb') as f:f.write(protect(key))
    public=api(s,'GET','/actions/secrets/public-key')
    box=SealedBox(PublicKey(base64.b64decode(public['key'])))
    api(s,'PUT','/actions/secrets/TINVEST_PAPER_STATE_KEY',expected=(201,204),json={'key_id':public['key_id'],
        'encrypted_value':base64.b64encode(box.encrypt(key)).decode()})
    branch=FuturesStore.branch
    check=s.get('https://api.github.com/repos/'+REPO+'/git/ref/heads/'+branch,timeout=20)
    if check.status_code==404:
        with tempfile.TemporaryDirectory() as folder:value=pack(seed(Path(folder)/'virtual.db'))
        encrypted=Fernet(key).encrypt(json.dumps(value).encode()).decode()
        tree=api(s,'POST','/git/trees',expected=(201,),json={'tree':[{'path':FuturesStore.filename,'mode':'100644','type':'blob','content':encrypted}]})
        commit=api(s,'POST','/git/commits',expected=(201,),json={'message':'Initialize isolated virtual RUB 1000000 futures account','tree':tree['sha'],'parents':[]})
        api(s,'POST','/git/refs',expected=(201,),json={'ref':'refs/heads/'+branch,'sha':commit['sha']})
    elif check.status_code==200:FuturesStore(REPO,s.headers['Authorization'][7:],key).load()
    else:raise ValueError('Cannot check separate cloud ledger')
    name='TINVEST_PAPER_ENABLED'
    check=s.get('https://api.github.com/repos/'+REPO+'/actions/variables/'+name,timeout=20)
    api(s,'PATCH' if check.status_code==200 else 'POST','/actions/variables'+('/'+name if check.status_code==200 else ''),
        expected=(201,204),json={'name':name,'value':'true'})
    # Only enable the new local projection after the encrypted state exists.
    envfile=ROOT/'.env';text=envfile.read_text(encoding='utf-8');name='TINVEST_PAPER_CLOUD_ENABLED'
    if re.search(r'^'+name+r'=',text,re.M):text=re.sub(r'^'+name+r'=.*$',name+'=true',text,flags=re.M)
    else:text=text.rstrip()+'\n'+name+'=true\n'
    envfile.write_text(text,encoding='utf-8')
    old_path=Path(dotenv_values(envfile).get('DB_PATH') or 'data/drafts.sqlite3')
    if not old_path.is_absolute():old_path=ROOT/old_path
    if old_path.exists():TradingLab(old_path).pause() # Preserve all old positions and journal; stop new legacy entries.
    print('Separate RUB 1000000 cloud simulator configured. Existing ledgers untouched; key DPAPI protected.')

if __name__=='__main__':
    try:main()
    except Exception as exc:raise SystemExit('Setup stopped: '+(str(exc) if type(exc) is ValueError else type(exc).__name__)+'. Secrets withheld.') from None
