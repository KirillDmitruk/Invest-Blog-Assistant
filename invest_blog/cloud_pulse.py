"""Private Pulse drafts on an ephemeral runner. Read-only broker; no publishing."""
from datetime import datetime,timedelta,timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import argparse,base64,hashlib,json,logging,os,re
import requests
from cryptography.fernet import Fernet
from .cloud_signals import json_call
from .posts import PostGenerator,current_week,previous_workweek,previous_month
from .publication_schedule import WEEKLY_RULES,DEFAULT_TIMES,LABELS
from .tinvest import TInvest,TInvestError

READS={('UsersService','GetAccounts'),('OperationsService','GetPortfolio'),('OperationsService','GetOperationsByCursor'),
       ('InstrumentsService','GetInstrumentBy'),('InstrumentsService','BondBy'),('InstrumentsService','GetBondCoupons'),
       ('InstrumentsService','GetDividends')}


class BrokerReadError(TInvestError):
    def __init__(self,method,status=None):
        self.method=method;self.status=status
        super().__init__('Broker read not confirmed')


class BrokerReadOnly(TInvest):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        bundle=Path(__file__).resolve().parents[1]/'deploy/tbank-ca.pem'
        if not bundle.is_file():raise ValueError('Нет доверенного хранилища Т-Банка.')
        self.session.verify=str(bundle)  # Scoped to this broker session, not Telegram/GitHub.
    def call(self,service,method,payload=None):
        if (service,method) not in READS:raise ValueError('Облачным черновикам разрешено только чтение данных.')
        try:return super().call(service,method,payload)
        except Exception as exc:
            status=re.search(r'\bHTTP (\d{3})\b',str(exc))
            raise BrokerReadError(method,int(status[1]) if status else None) from None


def due(now,times=DEFAULT_TIMES):
    if now.utcoffset() is None:raise ValueError('Нужен часовой пояс.')
    now=now.astimezone(ZoneInfo('Europe/Moscow'))
    kinds=[kind for kind,weekday,_ in WEEKLY_RULES if weekday==now.weekday()]
    if now.day==1:kinds.append('monthly')
    return [k for k in kinds if timedelta(0)<=now-now.replace(hour=times[k][0],minute=times[k][1],second=0,microsecond=0)<timedelta(hours=3)]


def period(kind,day):
    if kind=='weekly':
        a,b=previous_workweek(day);return a.isoformat()+'_'+b.isoformat()
    if kind=='monthly':return previous_month(day)[0].strftime('%Y-%m')
    return current_week(day)[0].isoformat()


class PrivateState:
    """Only authenticated ciphertext leaves the runner; key lives in Secrets."""
    branch='automation/pulse-drafts-state'
    filename='pulse-state.enc'
    def __init__(self,repo,token,commit,key,session=None):
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repo) or not re.fullmatch(r'[0-9a-f]{40}',commit):raise ValueError('Неверный репозиторий состояния.')
        self.url='https://api.github.com/repos/'+repo;self.commit=commit;self.session=session or requests.Session();self.sha=None
        self.headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json'};self.cipher=Fernet(key)
    def load(self):
        try:
            r=self.session.get(self.url+'/git/ref/heads/'+self.branch,headers=self.headers,timeout=20,allow_redirects=False);created=False
            if r.status_code==404:
                json_call(self.session,'POST',self.url+'/git/refs',headers=self.headers,json={'ref':'refs/heads/'+self.branch,'sha':self.commit});created=True
            elif r.status_code!=200:raise ValueError
            r=self.session.get(self.url+'/contents/'+self.filename,params={'ref':self.branch},headers=self.headers,timeout=20,allow_redirects=False)
            if r.status_code==404 and created:
                state={'version':1,'deliveries':{},'papers':[],'glossary':[]}
                self.save(state)  # A failed broker read must not leave an empty branch.
                return state
            if r.status_code!=200:raise ValueError
            v=r.json();self.sha=v['sha'];blob=base64.b64decode(v['content'])
            if len(blob)>1_000_000:raise ValueError
            state=json.loads(self.cipher.decrypt(blob))
            if state.get('version')!=1 or not isinstance(state.get('deliveries'),dict) or any(not isinstance(state.get(k),list) for k in ('papers','glossary')):raise ValueError
            return state
        except Exception:raise ValueError('Зашифрованное состояние недоступно или повреждено. Отправка запрещена.') from None
    def save(self,state):
        latest=sorted(state['deliveries'].items(),key=lambda item:(item[1].get('created_at',item[1]['period']),item[0]))[-100:]
        state={**state,'deliveries':dict(latest),'papers':state['papers'][:12],'glossary':state['glossary'][:12]}
        encrypted=self.cipher.encrypt(json.dumps(state,ensure_ascii=False).encode())
        body={'message':'Record encrypted Pulse delivery state','branch':self.branch,'content':base64.b64encode(encrypted).decode()}
        if self.sha:body['sha']=self.sha
        value=json_call(self.session,'PUT',self.url+'/contents/'+self.filename,headers=self.headers,json=body)
        self.sha=value['content']['sha']


class PrivateTelegram:
    def __init__(self,token,chat,session=None):
        if not token or not re.fullmatch(r'[1-9][0-9]{4,19}',chat):raise ValueError('Нужен ID личного чата.')
        self.session=session or requests.Session();self.url='https://api.telegram.org/bot'+token+'/';self.chat=chat
        me=json_call(self.session,'POST',self.url+'getMe',json={})
        user=json_call(self.session,'POST',self.url+'getChat',json={'chat_id':chat})
        if not me.get('ok') or not user.get('ok') or user['result'].get('type')!='private' or str(user['result']['id'])!=chat or user['result']['id']==me['result']['id']:raise ValueError('Личный получатель не подтверждён.')
    def send(self,draft):
        label=f"Черновик для Пульса · {LABELS[draft.kind]} · {draft.period}\nПроверь текст; публикация в Т-Инвестициях вручную.\n\n"
        text=label+draft.body
        if len(text)<=3900:
            value=json_call(self.session,'POST',self.url+'sendMessage',json={'chat_id':self.chat,'text':text,'disable_web_page_preview':True})
        else:
            value=json_call(self.session,'POST',self.url+'sendDocument',data={'chat_id':self.chat,'caption':label.strip()},
                files={'document':('pulse-draft.txt',draft.body.encode('utf-8'),'text/plain; charset=utf-8')})
        if not value.get('ok') or type(value.get('result',{}).get('message_id')) is not int:raise ValueError('Telegram не подтвердил отправку. Повтор заблокирован.')


def send_due(kinds,day,state_store,sender,generator_factory,recipient):
    state=state_store.load();result={}
    for kind in kinds:
        key=kind+':'+period(kind,day)+':'+hashlib.sha256(recipient.encode()).hexdigest()[:16]
        if key in state['deliveries']:result[kind]='already_attempted';continue
        generator=generator_factory(state)
        draft=generator.generate(kind,day)
        if draft.kind!=kind or draft.period!=period(kind,day) or not draft.body.strip():raise ValueError('Генератор вернул неподтверждённый черновик.')
        state['deliveries'][key]={'kind':kind,'period':draft.period,'status':'attempted','created_at':datetime.now(timezone.utc).isoformat()}
        topic=generator.selected_paper_uid if kind=='paper' else generator.selected_glossary_topic if kind=='glossary' else None
        if topic:
            field='papers' if kind=='paper' else 'glossary';state[field]=[topic]+[t for t in state[field] if t!=topic]
        state_store.save(state)  # Before any send, including multipart documents.
        sender.send(draft)
        state['deliveries'][key]['status']='sent';state_store.save(state);result[kind]='sent'
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--kind',choices=['auto',*DEFAULT_TIMES],default='auto');p.add_argument('--send',action='store_true');p.add_argument('--check',action='store_true');args=p.parse_args()
    # Never allow broker/HTTP debug logs or tracebacks into this public workflow.
    logging.disable(logging.CRITICAL)
    phase='configuration'
    try:
        now=datetime.now(ZoneInfo('Europe/Moscow'));kinds=due(now) if args.kind=='auto' else [args.kind]
        if not args.send and not args.check:print('Schedule check only. Due rubrics: '+(', '.join(kinds) or 'none'));return
        if not kinds and not args.check:print('No rubrics due in the three-hour window.');return
        token=os.environ['PULSE_TINVEST_TOKEN'];accounts=tuple(x.strip() for x in os.environ['PULSE_ACCOUNT_IDS'].split(',') if x.strip())
        if not token or not accounts:raise ValueError
        state=PrivateState(os.environ['GITHUB_REPOSITORY'],os.environ['GH_STATE_TOKEN'],os.environ['GITHUB_SHA'],os.environ['PULSE_STATE_KEY'].encode())
        phase='private_recipient'
        sender=PrivateTelegram(os.environ['SIGNALS_BOT_TOKEN'],os.environ['SIGNALS_CHAT_ID'])
        api=BrokerReadOnly(token,ZoneInfo('Europe/Moscow'))
        def factory(saved):return PostGenerator(api,accounts,Path('data/cbr_events.json'),tuple(saved['papers']),tuple(saved['glossary']))
        if args.check:
            phase='encrypted_state';saved=state.load();print('Encrypted state: verified.')
            phase='broker_accounts';available=api.accounts()
            if not set(accounts).issubset({a.get('id') for a in available}):raise ValueError
            print('Configured accounts: verified.')
            phase='broker_portfolio'
            for account in accounts:
                if not isinstance(api.portfolio(account).get('positions'),list):raise ValueError
            print('Portfolio read: verified.')
            phase='draft_generation'
            for kind in kinds:
                draft=factory(saved).generate(kind,now.date())
                if not draft.body.strip():raise ValueError
            print('Selected drafts generated in memory. No sends or draft artifacts.');return
        phase='generate_claim_send'
        result=send_due(kinds,now.date(),state,sender,factory,os.environ['SIGNALS_CHAT_ID'])
        for kind,status in result.items():print(kind+': '+status)
    except Exception as exc:
        detail=(' method='+exc.method+' http='+str(exc.status)) if isinstance(exc,BrokerReadError) else ''
        raise SystemExit('Pulse job stopped: phase='+phase+detail+'. Private response details withheld. No automatic send retry.') from None


if __name__=='__main__':main()
