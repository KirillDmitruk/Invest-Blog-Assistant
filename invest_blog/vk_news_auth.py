"""VK ID PKCE helpers and serialized refresh state; never print credentials."""
import base64,hashlib,json,secrets,time,re
from urllib.parse import urlencode
import requests
REDIRECT='http://localhost:8771/callback'
TOKEN_URL='https://id.vk.ru/oauth2/auth'

def challenge(verifier):return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')

def authorization_url(app_id,state,verifier):
    if not re.fullmatch(r'[1-9][0-9]{0,15}',str(app_id)):raise ValueError('Invalid application ID')
    return 'https://id.vk.ru/authorize?'+urlencode({'client_id':str(app_id),'redirect_uri':REDIRECT,'response_type':'code','scope':'wall groups','state':state,'code_challenge':challenge(verifier),'code_challenge_method':'s256'})

def token_reply(reply,state):
    if not isinstance(reply,dict) or reply.get('state')!=state:raise ValueError('OAuth state not confirmed')
    if not all(isinstance(reply.get(k),str) and reply[k] for k in ('access_token','refresh_token')):raise ValueError('OAuth credentials missing')
    if 'wall' not in re.split(r'[ ,]+',reply.get('scope','')):raise ValueError('VK did not grant wall permission')
    lifetime=int(reply.get('expires_in',0))
    if not 0<lifetime<=31_536_000:raise ValueError('Invalid token expiry')
    return {'access_token':reply['access_token'],'refresh_token':reply['refresh_token'],'user_id':str(reply['user_id']),'expires_at':time.time()+lifetime,'refresh_status':'ready'}

def exchange(app_id,code,device_id,state,verifier,session=None):
    response=(session or requests.Session()).post(TOKEN_URL,data={'grant_type':'authorization_code','client_id':str(app_id),'redirect_uri':REDIRECT,'code':code,'device_id':device_id,'state':state,'code_verifier':verifier},timeout=(5,30),allow_redirects=False)
    if response.status_code!=200:raise ValueError('VK ID exchange rejected')
    return {**token_reply(response.json(),state),'client_id':str(app_id),'device_id':device_id,'redirect_uri':REDIRECT}

def cloud_token(store,encoded,group_id,session=None):
    seed=json.loads(encoded)
    if seed.get('group_id')!=group_id:raise ValueError('OAuth destination mismatch')
    if not re.fullmatch(r'[1-9][0-9]{0,15}',str(seed.get('client_id',''))) or seed.get('redirect_uri')!=REDIRECT:raise ValueError('Invalid OAuth application')
    identity=hashlib.sha256(encoded.encode()).hexdigest();state=store.load();auth=state.get('vk_auth',{})
    if auth.get('seed_id')!=identity:
        auth={k:seed[k] for k in ('client_id','device_id','redirect_uri','access_token','refresh_token','user_id','expires_at')};auth.update(seed_id=identity,refresh_status='ready');state['vk_auth']=auth;store.save(state)
    if auth.get('refresh_status')!='ready':raise ValueError('VK refresh requires reconnect')
    if float(auth['expires_at'])>time.time()+120:return auth['access_token']
    nonce=secrets.token_urlsafe(24);auth['refresh_status']='attempted';store.save(state)
    response=(session or requests.Session()).post(TOKEN_URL,data={'grant_type':'refresh_token','client_id':auth['client_id'],'redirect_uri':REDIRECT,'device_id':auth['device_id'],'refresh_token':auth['refresh_token'],'state':nonce},timeout=(5,30),allow_redirects=False)
    if response.status_code!=200:raise ValueError('VK ID refresh rejected')
    updated=token_reply(response.json(),nonce)
    if updated['user_id']!=auth['user_id']:raise ValueError('VK account changed')
    auth.update(updated);store.save(state);return auth['access_token']
