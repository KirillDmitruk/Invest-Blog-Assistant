import pytest
from tools import deploy_demo_scheduler as module

@pytest.mark.parametrize('wrapped',[True,False])
def test_schedule_is_verified_for_both_api_envelopes(monkeypatch,wrapped):
    for key,value in {'TRADING_MODE':'paper','CF_ACCOUNT_ID':'a'*32,'GITHUB_REPOSITORY':'owner/repo',
                      'CF_WORKERS_DEPLOY_TOKEN':'fake','WALT_GH_DISPATCH_TOKEN':'fake'}.items():monkeypatch.setenv(key,value)
    calls=[]
    class Reply:
        status_code=200
        def json(self):
            schedules=[{'cron':'*/5 * * * *'}]
            return {'success':True,'result':{'schedules':schedules} if wrapped else schedules}
    class Session:
        def request(self,method,url,**kwargs):
            assert url.startswith('https://api.cloudflare.com/')
            calls.append((method,url,kwargs));return Reply()
    monkeypatch.setattr(module.requests,'Session',Session);module.main()
    assert len(calls)==3
    assert calls[1][2]['json']==[{'cron':'*/5 * * * *'}]
