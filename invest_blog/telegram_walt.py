"""Private Telegram -> fresh Walt preview -> separate one-shot confirmation."""
from html import escape
from pathlib import Path
from tempfile import TemporaryDirectory
import hashlib,json,logging,os,secrets,time
from .cloud_signals import json_call,previous_public_report
from .walt_telegram_protocol import button,verify_button,open_event,draft_card
from .walt_cloud_state import CloudWaltState,CloudWaltAgent
from .walt_trading import WaltTrading


class TradeBot:
    def __init__(self,service,owner,button_key,send,checkpoint):
        self.service=service;self.agent=service.agent;self.owner=str(owner);self.key=button_key
        self.send=send;self.checkpoint=checkpoint
        with self.agent.db() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS walt_tg_receipts(query_id TEXT PRIMARY KEY,status TEXT);
              CREATE TABLE IF NOT EXISTS walt_tg_buttons(nonce TEXT PRIMARY KEY,draft_id TEXT,preview_hash TEXT,
              expires REAL,owner TEXT,message_id TEXT,used INTEGER,kind TEXT);''')
    def handle(self,event,load_report=previous_public_report):
        parts=verify_button(event['data'],self.owner,self.key)
        if event.get('owner')!=self.owner or event.get('chat')!=self.owner:raise ValueError('Доступ ограничен.')
        with self.agent.db() as db:
            fresh=db.execute("INSERT OR IGNORE INTO walt_tg_receipts VALUES(?,'processing')",(event['query'],)).rowcount
        if not fresh:return 'duplicate'
        self.checkpoint()
        if parts[0]=='q':self.prepare(event,parts,load_report)
        elif parts[0] in ('e','c','r'):self.draft_action(event,parts)
        else:raise ValueError('Это действие обрабатывается отдельным сценарием.')
        with self.agent.db() as db:db.execute("UPDATE walt_tg_receipts SET status='done' WHERE query_id=?",(event['query'],))
        self.checkpoint();return 'done'
    def prepare(self,event,parts,load_report):
        report=load_report(parts[1]);ident=int(parts[2]);side='long' if parts[3]=='l' else 'short'
        from datetime import datetime
        age=time.time()-datetime.fromisoformat(report['created_at']).timestamp()
        if not 0<=age<=3*3600:raise ValueError('Обзор устарел. Запроси новый сигнал.')
        rows=[r for r in report['rows'] if r.get('market_id')==ident]
        if len(rows)!=1:raise ValueError('Инструмент не подтверждён в обзоре.')
        row=rows[0];sid=self.agent.connect();contract=self.service.catalog(sid).get(ident)
        if not contract or contract['base_asset']!=row['symbol']:raise ValueError('Точное соответствие инструмента Lighter и Walt не подтверждено.')
        # A public report is not a fresh Walt analysis. Preserve this distinction;
        # quote/prepare independently read actual price, balance, positions/limits.
        signal=secrets.token_hex(16);now=time.time()
        candidate={'contract_id':ident,'observed':now,'assessment':{'data_verified':False,'rule_side':'wait',
            'reason':'Ручной выбор из облачного обзора. Это не автоматический вход; расчёт по свежим данным Walt.'},
            'ai':{'side':'wait','reason':'Ручной выбор из облачного обзора.'}}
        packet={'id':signal,'created':now,'expires':now+180,'analysis_version':3,'candidates':[candidate]}
        with self.agent.db() as db:db.execute('INSERT INTO walt_trade_signals VALUES(?,?,?)',(signal,now,json.dumps(packet)))
        draft=self.service.quote(signal,ident,side,reserve_price_drift=True);preview=self.service.prepare(draft['id'])
        initial_leverage=preview['proposal']['leverage']
        while (preview['status']=='blocked' and preview['proposal']['leverage']>1
            and preview.get('liquidation_evidence',{}).get('source')=='local_conservative_estimate'
            and preview.get('block_reason')=='Стоп с учётом проскальзывания не имеет достаточного запаса до границы ликвидации. Исполнение заблокировано.'):
            # Only decrease leverage, keeping the exact same user risk/margin caps.
            # Every quote and preview independently re-read Walt. No execution.
            lower=preview['proposal']['leverage']-1
            draft=self.service.quote(signal,ident,side,leverage=lower,reserve_price_drift=True)
            preview=self.service.prepare(draft['id'])
        if preview['proposal']['leverage']<initial_leverage:
            notice=f'Плечо снижено с {initial_leverage}× до {preview["proposal"]["leverage"]}× для запаса защиты. Сумма и риск пересчитаны; лимиты не повышались. Проверь новые условия перед подтверждением.'
            row,private=self.service.row(preview['id']);private['telegram_notice']=notice
            private['warnings'].append(notice)
            with self.agent.db() as db:db.execute('UPDATE walt_trade_drafts SET payload=? WHERE id=?',(json.dumps(private,ensure_ascii=False),preview['id']))
            preview=self.service.public(preview['id'])
        self.checkpoint();self.show(preview)
    def register_button(self,draft,kind,expires):
        nonce=secrets.token_hex(8)
        with self.agent.db() as db:db.execute('INSERT INTO walt_tg_buttons VALUES(?,?,?,?,?,?,?,?)',
            (nonce,draft['id'],draft.get('preview_hash'),expires,self.owner,None,0,kind))
        return nonce
    def show(self,draft):
        keyboard=[];nonces=[]
        if draft['status']=='previewed':
            expiry=int(draft['preview_expires']);nonce=self.register_button(draft,'confirm',expiry);nonces.append(nonce)
            keyboard.append([{'text':'✅ Подтвердить открытие · реальные деньги','callback_data':button(f'e:{nonce}:{expiry}',self.owner,self.key)}])
            keyboard.append([{'text':'Отмена','callback_data':button(f'c:{nonce}:{expiry}',self.owner,self.key)}])
        if draft['status'] in ('previewed','submitted','executing','uncertain','protected','not_open'):
            expiry=int(time.time()+24*3600);nonce=self.register_button(draft,'read',expiry);nonces.append(nonce)
            keyboard.append([{'text':'🔄 Проверить заявку и позицию','callback_data':button(f'r:{nonce}:{expiry}',self.owner,self.key)}])
        self.checkpoint()
        message=self.send(draft_card(draft),{'inline_keyboard':keyboard})
        with self.agent.db() as db:
            for nonce in nonces:db.execute('UPDATE walt_tg_buttons SET message_id=? WHERE nonce=?',(str(message),nonce))
        self.checkpoint()
    def draft_action(self,event,parts):
        action,nonce,_=parts
        with self.agent.db() as db:r=db.execute('SELECT * FROM walt_tg_buttons WHERE nonce=?',(nonce,)).fetchone()
        if not r or r['owner']!=self.owner or r['message_id']!=str(event['message']) or time.time()>r['expires']:
            raise ValueError('Подтверждение не относится к этому сообщению или истекло.')
        if action=='r':
            if r['kind']!='read':raise ValueError('Нужна кнопка проверки состояния.')
            self.show(self.service.reconcile(r['draft_id']));return
        if r['kind']!='confirm':raise ValueError('Нужна отдельная кнопка подтверждения.')
        if r['used']:
            draft=self.service.public(r['draft_id'])
            if draft['status'] in ('executing','uncertain','submitted','protected','not_open'):draft=self.service.reconcile(r['draft_id'])
            self.show(draft if draft['status']!='previewed' else {**draft,'status':'blocked','block_reason':'Это подтверждение уже использовано. Сделай новый расчёт.'})
            return
        with self.agent.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('UPDATE walt_tg_buttons SET used=1 WHERE nonce=? AND used=0',(nonce,)).rowcount:raise ValueError('Подтверждение уже обрабатывается.')
        self.checkpoint()  # Consume before any external financial operation.
        if action=='c':
            with self.agent.db() as db:
                db.execute("UPDATE walt_trade_drafts SET status='cancelled' WHERE id=? AND status='previewed'",(r['draft_id'],))
                db.execute('DELETE FROM walt_agent_secrets WHERE name=?',('trade:'+r['draft_id'],))
            self.checkpoint();self.send('Расчёт отменён. Заявка не отправлялась.',{'inline_keyboard':[]});return
        # The core enforces limits revision, fresh account/price, expiry/hash,
        # unchanged protection and one-shot execution before it calls Walt.
        draft=self.service.execute(r['draft_id'],r['preview_hash'],'OPEN_REAL_POSITION')
        self.checkpoint();self.show(draft)


def main():
    logging.disable(logging.CRITICAL)
    try:
        owner=os.environ['SIGNALS_CHAT_ID'];bot=os.environ['SIGNALS_BOT_TOKEN']
        event=open_event(os.environ['WALT_EVENT'],os.environ['WALT_EVENT_KEY'],owner,os.environ['WALT_BUTTON_KEY'])
        def send(text,markup):
            result=json_call(__import__('requests').Session(),'POST','https://api.telegram.org/bot'+bot+'/sendMessage',
                json={'chat_id':owner,'text':text,'parse_mode':'HTML','reply_markup':markup})
            if not result.get('ok') or type(result.get('result',{}).get('message_id')) is not int:raise ValueError('Отправка ответа Telegram не подтверждена.')
            return result['result']['message_id']
        with TemporaryDirectory(prefix='walt-private-') as folder:
            state=CloudWaltState(Path(folder)/'walt.sqlite3',os.environ['GITHUB_REPOSITORY'],os.environ['GH_STATE_TOKEN'],os.environ['WALT_CLOUD_STATE_KEY'].encode())
            state.restore();agent=CloudWaltAgent(state);service=WaltTrading(agent)
            controller=TradeBot(service,owner,os.environ['WALT_BUTTON_KEY'],send,state.checkpoint)
            try:controller.handle(event)
            except ValueError as exc:
                state.checkpoint();send('Операция остановлена: '+escape(str(exc))+'\nАвтоматического повтора нет.',{'inline_keyboard':[]})
                print('Cloud Walt action stopped safely. Private details withheld.');return
            print('Cloud Walt callback processed. Private details withheld.')
    except Exception:raise SystemExit('Cloud Walt callback not confirmed. No automatic retry. Details withheld.') from None


if __name__=='__main__':main()
