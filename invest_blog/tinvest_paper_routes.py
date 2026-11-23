"""Read-only local projection of the separate encrypted RUB cloud ledger."""
import json
import tempfile
import threading
import time
from pathlib import Path

import requests
from cryptography.fernet import Fernet
from .walt_agent import protect
from .tinvest_paper_cloud import validate_envelope
from .trading_lab import TradingLab
from .demo_snapshot import encrypted_snapshot


class FuturesDashboard:
    def __init__(self,path,session=None):
        self.key=Path(path).parent/'tinvest-paper-key.dpapi'
        self.session=session or requests.Session();self.cached=None;self.checked=0;self.lock=threading.Lock()

    def overview(self,force=False):
        with self.lock:
            if not force and self.cached and time.time()-self.checked<300:return {**self.cached,'sync':'cached'}
            try:
                key=protect(self.key.read_bytes(),True)
                encrypted=encrypted_snapshot(self.session,'automation/tinvest-paper-state','tinvest-paper.enc',force)
                raw=validate_envelope(json.loads(Fernet(key).decrypt(encrypted)))
                with tempfile.TemporaryDirectory() as folder:
                    path=Path(folder)/'virtual.db';path.write_bytes(raw);value=TradingLab(path).overview()
                if value['state'].get('cloud_managed') is not True or value['state']['budget']!='1000000':raise ValueError('Wrong virtual account')
                value.update(cloud_mode=True,sync='ok',ai={'provider':'Cloudflare','model':value['state']['cloud'].get('model','Gemma'), 'configured':True})
                value['notes']+=['Облако: мониторинг примерно раз в 15 минут, анализ ИИ раз в 2 часа. Запуски могут задерживаться.',
                    'Стоп — 2 ATR, цель — 4 ATR; исполнение по минутным свечам приблизительное. При касании обоих уровней сначала стоп.',
                    'Комиссия в модели: 2 ₽ за контракт на сторону; проскальзывание — 1 тик. Это не тариф брокера.',
                    'Бюджет отдельный: 1 000 000 ₽. Старая локальная история сохранена. ИИ не получает реальные счета или баланс.']
                self.checked=time.time();value['fetched_at']=self.checked;self.cached=value;return value
            except Exception:
                if self.cached:return {**self.cached,'sync':'unavailable','notes':self.cached['notes']+['Связь с облаком недоступна: показан сохранённый снимок.']}
                raise ValueError('Виртуальный счёт пока недоступен. Проверь облачный цикл; пустой счёт вместо данных не создаётся.') from None
