import json,base64
import pytest
from cryptography.fernet import Fernet
from invest_blog.paper_perps import initial
from invest_blog.paper_routes import PaperDashboard
from invest_blog.tinvest_paper_routes import FuturesDashboard
from invest_blog.tinvest_paper_cloud import seed,pack


@pytest.mark.parametrize('kind',['walt','tinvest'])
def test_manual_refresh_bypasses_cache_and_preserves_old_snapshot_on_error(tmp_path,monkeypatch,kind):
    key=Fernet.generate_key()
    if kind=='walt':
        import invest_blog.paper_routes as module
        keyname='paper-state-key.dpapi';factory=PaperDashboard;value=initial()
    else:
        import invest_blog.tinvest_paper_routes as module
        keyname='tinvest-paper-key.dpapi';factory=FuturesDashboard;value=pack(seed(tmp_path/'seed.db'))
    monkeypatch.setattr(module,'protect',lambda *args:key)
    (tmp_path/keyname).write_bytes(b'protected')
    class Reply:
        status_code=200
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def raise_for_status(self):pass
        def iter_content(self,size):yield json.dumps({'encoding':'base64','content':base64.b64encode(Fernet(key).encrypt(json.dumps(value).encode())).decode()}).encode()
    class Session:
        calls=[];failed=False
        def get(self,url,**kwargs):
            self.calls.append(kwargs)
            if self.failed:raise ValueError('Network unavailable')
            return Reply()
    session=Session();dashboard=factory(tmp_path/'hub.db',session)
    first=dashboard.overview();assert first['fetched_at']>0
    assert dashboard.overview()['sync']=='cached' and len(session.calls)==1
    fresh=dashboard.overview(force=True)
    assert len(session.calls)==2 and fresh['sync']=='ok'
    assert session.calls[0]['params']['v']!=session.calls[1]['params']['v']
    session.failed=True;failed=dashboard.overview(force=True)
    assert failed['sync']=='unavailable' and failed['fetched_at']==fresh['fetched_at']
    assert failed['state']==fresh['state']
