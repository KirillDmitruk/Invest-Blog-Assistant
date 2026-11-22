"""Deploy stateless callback gateway. Explicit activation changes Telegram webhook."""
import base64,hashlib,json,os,re,sqlite3,sys
from pathlib import Path
import requests

def required(name):
    value=os.environ.get(name,'').strip()
    if not value:raise ValueError('Missing configuration: '+name)
    return value

def run():
    account=required('CF_ACCOUNT_ID')
    if not re.fullmatch('[a-f0-9]{32}',account):raise ValueError('Invalid account')
    repo=required('GITHUB_REPOSITORY');owner=required('SIGNALS_CHAT_ID');bot=required('SIGNALS_BOT_TOKEN')
    active=os.getenv('ACTIVATE_WALT_WEBHOOK')=='true'
    script='content-hub-walt-callbacks';session=requests.Session()
    cf='https://api.cloudflare.com/client/v4/accounts/'+account
    headers={'Authorization':'Bearer '+required('CF_WORKERS_DEPLOY_TOKEN')}
    def call(method,url,**kwargs):
        response=session.request(method,url,timeout=(5,30),**kwargs)
        service='Cloudflare' if url.startswith('https://api.cloudflare.com/') else 'GitHub' if url.startswith('https://api.github.com/') else 'Telegram'
        if response.status_code>=300:
            codes=[]
            if service=='Cloudflare':
                try:codes=[e.get('code') for e in response.json().get('errors',[]) if type(e.get('code')) is int]
                except Exception:pass
            stage='upload' if 'files' in kwargs else 'account-subdomain' if url==cf+'/workers/subdomain' else 'script-subdomain' if '/subdomain' in url else 'request'
            raise ValueError(service+' '+stage+' HTTP '+str(response.status_code)+' codes '+str(codes))
        value=response.json()
        if value.get('success') is False or value.get('ok') is False:raise ValueError(service+' setup not confirmed')
        return value
    me=call('POST','https://api.telegram.org/bot'+bot+'/getMe',json={})['result']
    chat=call('POST','https://api.telegram.org/bot'+bot+'/getChat',json={'chat_id':owner})['result']
    if str(chat['id'])!=owner or chat.get('type')!='private' or str(me['id'])==owner:raise ValueError('Owner not confirmed')
    secrets={'TG_BOT_TOKEN':bot,'OWNER_ID':owner,'BOT_ID':str(me['id']),
        'TG_WEBHOOK_SECRET':required('TG_WEBHOOK_SECRET'),'WALT_BUTTON_KEY':required('WALT_BUTTON_KEY'),
        'WALT_EVENT_KEY':required('WALT_EVENT_KEY'),'GH_DISPATCH_TOKEN':required('WALT_GH_DISPATCH_TOKEN')}
    if active:
        # No empty/new OAuth account on activation; require imported funded identity.
        from cryptography.fernet import Fernet
        cipher=Fernet(required('WALT_CLOUD_STATE_KEY').encode())
        gh='https://api.github.com/repos/'+repo
        gh_headers={'Authorization':'Bearer '+required('GH_STATE_TOKEN')}
        content=call('GET',gh+'/contents/walt-state.enc',headers=gh_headers,params={'ref':'automation/walt-private-state'})
        if not content.get('content'):content=call('GET',gh+'/git/blobs/'+content['sha'],headers=gh_headers)
        plain=cipher.decrypt(base64.b64decode(content['content']))
        if len(plain)>8_000_000:raise ValueError('State too large')
        db=sqlite3.connect(':memory:')
        try:
            db.deserialize(plain)
            values={name:json.loads(cipher.decrypt(bytes(value))) for name,value in db.execute("SELECT name,value FROM walt_agent_secrets WHERE name IN ('client','tokens')")}
            if not values.get('client',{}).get('client_id') or 'perps.trade' not in values.get('tokens',{}).get('scope','').split():raise ValueError('Trading identity not imported')
        finally:db.close()
    bindings=[{'type':'secret_text','name':name,'text':value} for name,value in secrets.items()]
    bindings += [{'type':'plain_text','name':'GITHUB_REPOSITORY','text':repo},
        {'type':'plain_text','name':'ENABLED','text':'true' if active else 'false'}]
    metadata={'main_module':'worker.mjs','compatibility_date':'2026-11-22','bindings':bindings,
        'observability':{'enabled':False},'logpush':False}
    code=(Path(__file__).resolve().parents[1]/'cloudflare/walt-webhook/worker.mjs').read_bytes()
    print('Uploading disabled gateway.' if not active else 'Uploading gateway for approved activation.')
    call('PUT',cf+'/workers/scripts/'+script,headers=headers,files={
        'metadata':('metadata.json',json.dumps(metadata),'application/json'),
        'worker.mjs':('worker.mjs',code,'application/javascript+module')})
    try:subdomain=call('GET',cf+'/workers/subdomain',headers=headers)['result']['subdomain']
    except ValueError as exc:
        if str(exc)!='Cloudflare account-subdomain HTTP 404 codes [10007]':raise
        name='kirill-content-hub-'+hashlib.sha256(account.encode()).hexdigest()[:8]
        subdomain=call('PUT',cf+'/workers/subdomain',headers=headers,json={'subdomain':name})['result']['subdomain']
        print('Initialized free workers.dev account address.')
    call('POST',cf+'/workers/scripts/'+script+'/subdomain',headers=headers,json={'enabled':True,'previews_enabled':False})
    if not re.fullmatch('[a-z0-9-]+',subdomain):raise ValueError('Subdomain not confirmed')
    url='https://'+script+'.'+subdomain+'.workers.dev'
    health=session.get(url,timeout=(5,30),allow_redirects=False)
    if health.status_code!=200 or health.text!='Walt callback gateway':raise ValueError('Public gateway health not confirmed')
    denied=session.post(url,json={},timeout=(5,30),allow_redirects=False)
    if denied.status_code!=403:raise ValueError('Unsigned gateway request not rejected')
    if active:
        # This explicit switch disables getUpdates/local polling for this bot.
        call('POST','https://api.telegram.org/bot'+bot+'/setWebhook',json={
            'url':url,'secret_token':secrets['TG_WEBHOOK_SECRET'],'allowed_updates':['callback_query'],
            'drop_pending_updates':False,'max_connections':1})
        info=call('POST','https://api.telegram.org/bot'+bot+'/getWebhookInfo',json={})['result']
        if info.get('url')!=url:raise ValueError('Webhook not verified')
    print('Gateway deployed; '+('webhook active.' if active else 'trading disabled; webhook unchanged.'))
    print('Public gateway URL: '+url)

if __name__=='__main__':
    try:run()
    except Exception as exc:raise SystemExit('Gateway setup not confirmed: '+(str(exc) if type(exc) is ValueError else type(exc).__name__)+'. Secrets withheld; no automatic retry.') from None
