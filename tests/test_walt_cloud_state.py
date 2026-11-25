import base64,json,sqlite3,time
from cryptography.fernet import Fernet
from invest_blog.walt_cloud_state import CloudWaltState

class Reply:
    status_code=200
    def __init__(self,value):self.value=value
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def iter_content(self,size):
        encoded=json.dumps(self.value).encode()
        for start in range(0,len(encoded),size):yield encoded[start:start+size]

def test_large_blob_restore_and_expired_cache_compaction(tmp_path):
    key=Fernet.generate_key();cipher=Fernet(key);db=sqlite3.connect(':memory:')
    db.executescript('CREATE TABLE walt_trade_signals(id TEXT,at REAL,payload TEXT); CREATE TABLE journal(payload TEXT);')
    db.execute('INSERT INTO walt_trade_signals VALUES(?,?,?)',('old',0,json.dumps({'expires':1,'padding':'x'*2_500_000})))
    db.execute('INSERT INTO journal VALUES(?)',('preserved',));db.commit()
    encrypted=cipher.encrypt(db.serialize());db.close();saved=[]
    class Session:
        def request(self,method,url,**kwargs):
            if method=='PUT':
                saved.append(kwargs['json']);return Reply({'content':{'sha':'new'}})
            if '/contents/' in url:return Reply({'sha':'old','content':''})
            return Reply({'content':base64.b64encode(encrypted).decode()})
    path=tmp_path/'state.db';store=CloudWaltState(path,'a/b','fake',key,Session());store.restore();store.checkpoint()
    assert saved[0]['sha']=='old' and len(base64.b64decode(saved[0]['content']))<100_000
    restored=sqlite3.connect(':memory:');restored.deserialize(cipher.decrypt(base64.b64decode(saved[0]['content'])))
    assert restored.execute('SELECT payload FROM journal').fetchone()[0]=='preserved'
    assert restored.execute('SELECT count(*) FROM walt_trade_signals').fetchone()[0]==0
    restored.close();path.unlink() # No leaked SQLite handles on Windows.
