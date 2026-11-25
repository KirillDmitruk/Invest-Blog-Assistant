"""Encrypted cloud-only SQLite state with durable checkpoints before execution."""
from pathlib import Path
from contextlib import closing
import base64,json,re,sqlite3,time
import requests
from cryptography.fernet import Fernet
from .cloud_signals import json_call
from .walt_agent import WaltAgent


class CloudWaltState:
    branch='automation/walt-private-state'
    filename='walt-state.enc'
    maximum=8_000_000
    def __init__(self,path,repo,token,key,session=None):
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repo):raise ValueError('Неверный репозиторий.')
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True);self.cipher=Fernet(key)
        self.url='https://api.github.com/repos/'+repo;self.headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json'}
        self.session=session or requests.Session();self.sha=None
    def restore(self):
        try:
            meta=json_call(self.session,'GET',self.url+'/contents/'+self.filename,headers=self.headers,params={'ref':self.branch})
            self.sha=meta['sha']
            if not meta.get('content'):
                meta=json_call(self.session,'GET',self.url+'/git/blobs/'+self.sha,headers=self.headers,response_limit=self.maximum*2)
            encrypted=base64.b64decode(meta['content'])
            if len(encrypted)>self.maximum*2:raise ValueError
            plain=self.cipher.decrypt(encrypted)
            if len(plain)>self.maximum or not plain.startswith(b'SQLite format 3\0'):raise ValueError
            self.path.write_bytes(plain)
            with closing(sqlite3.connect(self.path)) as db:
                if db.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError
        except Exception:raise ValueError('Состояние облачного Walt недоступно. Новое пустое состояние не создаётся; сделки запрещены.') from None
    def checkpoint(self):
        if not self.sha:raise ValueError('Облачное состояние не загружено.')
        with closing(sqlite3.connect(self.path)) as db:
            # Expired scan packets are a cache; keep all trade drafts/journal.
            db.execute("DELETE FROM walt_trade_signals WHERE json_extract(payload,'$.expires') < ?",(time.time(),))
            db.commit();db.execute('VACUUM');plain=db.serialize()
        if len(plain)>self.maximum:raise ValueError('Превышен размер состояния. Исполнение остановлено.')
        encrypted=self.cipher.encrypt(plain)
        value=json_call(self.session,'PUT',self.url+'/contents/'+self.filename,headers=self.headers,json={
            'branch':self.branch,'sha':self.sha,'message':'Checkpoint encrypted Walt state',
            'content':base64.b64encode(encrypted).decode()})
        self.sha=value['content']['sha']
    def crypto(self,data,decrypt=False):return self.cipher.decrypt(data) if decrypt else self.cipher.encrypt(data)


class CloudWaltAgent(WaltAgent):
    cloud_execution_backend=True
    def __init__(self,store):
        self.cloud_store=store;super().__init__(store.path,crypto=store.crypto)
    def save_tokens(self,*args,**kwargs):
        super().save_tokens(*args,**kwargs)
        self.cloud_store.checkpoint()  # Persist refresh-token rotation before using it.
    def rpc(self,method,params=None,session_id=None,_manual=False):
        name=params.get('name') if isinstance(params,dict) else None
        if name=='perps_execute_operation':
            if not _manual:raise ValueError('Нужно ручное подтверждение.')
            # Core has already stored executing + attempted_at. If this fails,
            # do NOT call Walt. A lost post-send response remains durable executing.
            self.cloud_store.checkpoint()
        return super().rpc(method,params,session_id,_manual)
