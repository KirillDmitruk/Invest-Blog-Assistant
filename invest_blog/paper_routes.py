"""Local read-only dashboard of the encrypted cloud PAPER account."""
from pathlib import Path
import json,time,threading,os
import requests
from cryptography.fernet import Fernet
from .walt_agent import protect

REPO='KirillDmitruk/Invest-Blog-Assistant'

class PaperDashboard:
    def __init__(self,path,session=None):
        self.key_path=Path(path).parent/'paper-state-key.dpapi';self.session=session or requests.Session()
        self.cached=None;self.checked=0;self.lock=threading.Lock()
    def overview(self):
        with self.lock:
            if self.cached and time.time()-self.checked<120:return {**self.cached,'sync':'cached'}
            if not self.key_path.exists():return {'configured':False,'mode':'paper','message':'Ключ чтения виртуального счёта ещё не установлен.'}
            try:
                key=protect(self.key_path.read_bytes(),True)
                url='https://raw.githubusercontent.com/'+REPO+'/refs/heads/automation/paper-perps-state/paper-state.enc'
                with self.session.get(url,params={'v':int(time.time()//120)},headers={'Cache-Control':'no-cache'},timeout=(5,15),allow_redirects=False,stream=True) as r:
                    if r.status_code!=200:raise ValueError
                    chunks=[];size=0
                    for b in r.iter_content(65536):
                        size+=len(b)
                        if size>2_000_000:raise ValueError
                        chunks.append(b)
                state=json.loads(Fernet(key).decrypt(b''.join(chunks)))
                if state.get('mode')!='paper' or state.get('initial_balance')!='10000':raise ValueError
                self.cached={'configured':True,'mode':'paper','state':state};self.checked=time.time()
                return {**self.cached,'sync':'ok'}
            except Exception:
                if self.cached:return {**self.cached,'sync':'unavailable','message':'Облако недоступно. Показан последний полученный снимок.'}
                return {'configured':True,'mode':'paper','sync':'unavailable','message':'Не удалось получить виртуальный счёт. Реальные сделки отключены.'}

def register_paper_routes(app,path):
    service=PaperDashboard(path)
    @app.get('/api/paper/overview')
    def paper_overview():return {**service.overview(),'real_execution_disabled':os.getenv('TRADING_MODE')=='paper'}
