import base64,json,sqlite3
import pytest
from cryptography.fernet import Fernet
from tools.export_walt_cloud import snapshot

def test_export_preserves_agent_identity_and_excludes_other_hub_data(tmp_path):
    path=tmp_path/'hub.db';key=Fernet.generate_key();cipher=Fernet(key)
    with sqlite3.connect(path) as db:
        db.executescript('''CREATE TABLE walt_agent_state(id INTEGER,payload TEXT);
          CREATE TABLE walt_agent_secrets(name TEXT,value BLOB);
          CREATE TABLE walt_trade_drafts(id TEXT,status TEXT,payload TEXT);
          CREATE TABLE private_broker_portfolio(account TEXT); INSERT INTO private_broker_portfolio VALUES('never-export');
          INSERT INTO walt_agent_state VALUES(1,'{}');
          INSERT INTO walt_trade_drafts VALUES('old','previewed','{}');''')
        for name,value in [('client',{'client_id':'same-funded-agent'}),('tokens',{'scope':'perps.readonly perps.trade','refresh_token':'same-refresh'}),('trade:old',{'token':'never-export'})]:
            db.execute('INSERT INTO walt_agent_secrets VALUES(?,?)',(name,base64.b64encode(json.dumps(value).encode())))
    encrypted=snapshot(path,key,lambda value,decrypt:base64.b64decode(value))
    assert b'never-export' not in encrypted and b'same-refresh' not in encrypted
    db=sqlite3.connect(':memory:');db.deserialize(cipher.decrypt(encrypted))
    tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert 'private_broker_portfolio' not in tables
    secrets={name:json.loads(cipher.decrypt(value)) for name,value in db.execute('SELECT * FROM walt_agent_secrets')}
    assert secrets=={'client':{'client_id':'same-funded-agent'},'tokens':{'scope':'perps.readonly perps.trade','refresh_token':'same-refresh'}}
    assert db.execute('SELECT status FROM walt_trade_drafts').fetchone()[0]=='expired'
    with sqlite3.connect(path) as original:
        assert original.execute('SELECT status FROM walt_trade_drafts').fetchone()[0]=='previewed'
        original.execute("UPDATE walt_trade_drafts SET status='uncertain'")
    with pytest.raises(ValueError,match='неподтверждённая'):snapshot(path,key,lambda value,decrypt:base64.b64decode(value))
