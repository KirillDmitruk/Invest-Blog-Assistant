"""Local read-only dashboard of the encrypted cloud PAPER account."""
from pathlib import Path
import json,time,threading,os
import requests
from cryptography.fernet import Fernet
from .walt_agent import protect
from .demo_snapshot import encrypted_snapshot

REPO='KirillDmitruk/Invest-Blog-Assistant'

class PaperDashboard:
    def __init__(self,path,session=None):
        self.key_path=Path(path).parent/'paper-state-key.dpapi';self.session=session or requests.Session()
        self.cached=None;self.checked=0;self.lock=threading.Lock()
    def overview(self,force=False):
        with self.lock:
            if not force and self.cached and time.time()-self.checked<300:return {**self.cached,'sync':'cached','fetched_at':self.checked}
            if not self.key_path.exists():return {'configured':False,'mode':'paper','message':'Ключ чтения виртуального счёта ещё не установлен.'}
            try:
                key=protect(self.key_path.read_bytes(),True)
                raw=encrypted_snapshot(self.session,'automation/paper-perps-state','paper-state.enc',force)
                state=json.loads(Fernet(key).decrypt(raw))
                if state.get('mode')!='paper' or state.get('initial_balance')!='10000':raise ValueError
                self.checked=time.time();self.cached={'configured':True,'mode':'paper','state':state,'fetched_at':self.checked}
                return {**self.cached,'sync':'ok'}
            except Exception:
                if self.cached:return {**self.cached,'sync':'unavailable','message':'Облако недоступно. Показан последний полученный снимок.'}
                return {'configured':True,'mode':'paper','sync':'unavailable','message':'Не удалось получить виртуальный счёт. Реальные сделки отключены.'}

def register_paper_routes(app,path):
    service=PaperDashboard(path)
    @app.get('/api/paper/overview')
    def paper_overview(refresh:bool=False):return {**service.overview(force=refresh),'real_execution_disabled':os.getenv('TRADING_MODE')=='paper'}
