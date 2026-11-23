"""Provision a separate $10k PAPER account. No brokerage calls or real-state edits."""
import base64,json,re,sys
from pathlib import Path
from cryptography.fernet import Fernet
from nacl.public import PublicKey,SealedBox
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.provision_walt_cloud import github,api,REPO,ROOT
from invest_blog.walt_agent import protect
from invest_blog.paper_perps import initial
from invest_blog.paper_cloud import PaperStore

def main():
    s=github();keyfile=ROOT/'data/paper-state-key.dpapi'
    if keyfile.exists():key=protect(keyfile.read_bytes(),True)
    else:
        existing=api(s,'GET','/actions/secrets')['secrets']
        if any(x['name']=='PAPER_STATE_KEY' for x in existing):raise ValueError('Existing paper key must not be overwritten')
        key=Fernet.generate_key();keyfile.parent.mkdir(parents=True,exist_ok=True)
        with keyfile.open('xb') as handle:handle.write(protect(key))
    public=api(s,'GET','/actions/secrets/public-key');box=SealedBox(PublicKey(base64.b64decode(public['key'])))
    api(s,'PUT','/actions/secrets/PAPER_STATE_KEY',expected=(201,204),json={
        'key_id':public['key_id'],'encrypted_value':base64.b64encode(box.encrypt(key)).decode()})
    branch=PaperStore.branch
    r=s.get('https://api.github.com/repos/'+REPO+'/git/ref/heads/'+branch,timeout=20)
    if r.status_code==404:
        encrypted=Fernet(key).encrypt(json.dumps(initial()).encode()).decode()
        tree=api(s,'POST','/git/trees',expected=(201,),json={'tree':[{'path':PaperStore.filename,'mode':'100644','type':'blob','content':encrypted}]})
        commit=api(s,'POST','/git/commits',expected=(201,),json={'message':'Initialize separate virtual USD 10000 account','tree':tree['sha'],'parents':[]})
        api(s,'POST','/git/refs',expected=(201,),json={'ref':'refs/heads/'+branch,'sha':commit['sha']})
    elif r.status_code==200:
        PaperStore(REPO,s.headers['Authorization'][7:],key).load() # Verify; never reset an existing account.
    else:raise ValueError('State branch unavailable')
    for name,value in [('TRADING_MODE','paper'),('PAPER_ENABLED','true'),('WALT_TELEGRAM_ENABLED','false')]:
        check=s.get('https://api.github.com/repos/'+REPO+'/actions/variables/'+name,timeout=20)
        method='PATCH' if check.status_code==200 else 'POST';path='/actions/variables'+('/'+name if method=='PATCH' else '')
        api(s,method,path,expected=(201,204),json={'name':name,'value':value})
    api(s,'PUT','/actions/workflows/cloud-walt.yml/disable',expected=(204,))
    envfile=ROOT/'.env';text=envfile.read_text(encoding='utf-8')
    if re.search(r'^TRADING_MODE=',text,re.M):text=re.sub(r'^TRADING_MODE=.*$','TRADING_MODE=paper',text,flags=re.M)
    else:text=text.rstrip()+'\nTRADING_MODE=paper\n'
    envfile.write_text(text,encoding='utf-8')
    ignore=ROOT/'.gitignore';text=ignore.read_text(encoding='utf-8')
    if 'data/paper-state-key.dpapi' not in text:ignore.write_text(text.rstrip()+'\ndata/paper-state-key.dpapi\n',encoding='utf-8')
    print('Separate encrypted PAPER account ready; start USD 10000. Local read key DPAPI-protected. Real workflow remains disabled.')

if __name__=='__main__':
    try:main()
    except Exception as exc:raise SystemExit('Paper setup stopped: '+type(exc).__name__+'. Keys withheld; no real operations.') from None
