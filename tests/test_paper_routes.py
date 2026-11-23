import json,base64
from pathlib import Path
from cryptography.fernet import Fernet
from invest_blog.paper_perps import initial
from invest_blog.paper_routes import PaperDashboard

def test_no_local_key_is_explicit_not_a_fake_account(tmp_path):
    assert PaperDashboard(tmp_path/'hub.db').overview()['configured'] is False

def test_dashboard_loads_only_encrypted_paper_snapshot(tmp_path,monkeypatch):
    import invest_blog.paper_routes as module
    key=Fernet.generate_key();(tmp_path/'paper-state-key.dpapi').write_bytes(b'protected')
    monkeypatch.setattr(module,'protect',lambda data,decrypt:key)
    class Reply:
        status_code=200
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def iter_content(self,size):yield json.dumps({'encoding':'base64','content':base64.b64encode(Fernet(key).encrypt(json.dumps(initial()).encode())).decode()}).encode()
    class Session:
        def get(self,url,**kw):
            assert 'paper-state.enc' in url and 'walletbot' not in url
            return Reply()
    d=PaperDashboard(tmp_path/'hub.db',Session());assert d.overview()['state']['cash']=='10000'
    assert d.overview()['sync']=='cached'
