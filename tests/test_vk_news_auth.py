import json,time
from copy import deepcopy
from urllib.parse import parse_qs,urlsplit
import pytest
from invest_blog.vk_news_auth import authorization_url,challenge,token_reply,cloud_token
from test_cloud_news import Store

def test_pkce_url_requests_only_needed_scopes_and_uses_official_endpoint():
    p=urlsplit(authorization_url('123','nonce','a'*64));q=parse_qs(p.query)
    assert p.netloc=='id.vk.ru' and q['scope']==['wall groups']
    assert q['code_challenge']==[challenge('a'*64)] and 'a'*64 not in p.query

def test_token_reply_rejects_wrong_state_missing_wall_and_missing_refresh():
    r={'state':'ok','scope':'wall groups','expires_in':3600,'access_token':'a','refresh_token':'r','user_id':1}
    assert token_reply(r,'ok')['refresh_status']=='ready'
    for patch in [{'state':'wrong'},{'scope':'email'},{'refresh_token':''}]:
        with pytest.raises(ValueError):token_reply({**r,**patch},'ok')

def bundle():return json.dumps({'group_id':243744746,'client_id':'123','device_id':'d','redirect_uri':'http://localhost:8771/callback','access_token':'a','refresh_token':'r','user_id':'1','expires_at':time.time()-1})

def test_refresh_is_durable_rotates_tokens_and_is_not_retried_after_timeout():
    s=Store();calls=[]
    class Session:
        def post(self,url,data,**kwargs):
            assert s.state['vk_auth']['refresh_status']=='attempted';calls.append(url)
            raise TimeoutError('secret')
    with pytest.raises(TimeoutError):cloud_token(s,bundle(),243744746,Session())
    # Keep exactly the same seed, as a GitHub secret does.
    encoded=bundle();s=Store();calls=[]
    with pytest.raises(TimeoutError):cloud_token(s,encoded,243744746,Session())
    with pytest.raises(ValueError):cloud_token(s,encoded,243744746,Session())
    assert len(calls)==1

def test_successful_refresh_persists_replacement_and_reuses_it():
    s=Store();encoded=bundle();calls=[]
    class Session:
        def post(self,url,data,**kwargs):
            calls.append(url)
            class Response:
                status_code=200
                def json(self):return {'state':data['state'],'scope':'wall groups','access_token':'new','refresh_token':'rotated','expires_in':3600,'user_id':1}
            return Response()
    assert cloud_token(s,encoded,243744746,Session())=='new'
    assert cloud_token(s,encoded,243744746,Session())=='new'
    assert len(calls)==1 and s.state['vk_auth']['refresh_token']=='rotated'
