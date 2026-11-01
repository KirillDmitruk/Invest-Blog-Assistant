"""Persistent monthly investment plans. Monetary amounts stored as integer kopecks/cents."""
from __future__ import annotations

import calendar
import sqlite3
from datetime import datetime
import xml.etree.ElementTree as ET
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import httpx

WEIGHTS = [('Рынок РФ',20),('Зарубежные активы',45),('Криптовалюта',20),('Золото',10),('Трейдинг / боты',5)]
FOREIGN = [('S&P 500',40),('SCHF',20),('IEMG',20),('Отдельные акции',20)]
CRYPTO = [('BTC',50),('ETH',25),('SOL',10),('TON',5),('BNB',5),('TRON',5)]


def split(total: int, weights: list[int]) -> list[int]:
    # Largest remainder; exact conservation and no negative trailing amount.
    divs = [total * w // sum(weights) for w in weights]
    rest = total - sum(divs)
    order = sorted(range(len(weights)), key=lambda i: (-(total * weights[i] % sum(weights)), i))
    for idx in order[:rest]:
        divs[idx] += 1
    return divs


def as_minor(value: object) -> int:
    d=Decimal(str(value))
    if not d.is_finite() or not (0 <= d <= Decimal('1000000000000')):
        raise ValueError('Некорректная сумма')
    return int((d*100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def fetch_cbr_usd() -> dict:
    """Official CBR latest daily XML. Fail closed; no fabricated or stale FX rate."""
    resp=httpx.get('https://www.cbr.ru/scripts/XML_daily.asp',timeout=12,follow_redirects=True)
    resp.raise_for_status()
    root=ET.fromstring(resp.content)
    valute=next((e for e in root.findall('Valute') if e.findtext('CharCode')=='USD'),None)
    if valute is None:
        raise ValueError('USD отсутствует в данных ЦБ')
    nominal=Decimal(valute.findtext('Nominal','1'))
    rate=Decimal(valute.findtext('Value','').replace(',','.'))/nominal
    if not rate.is_finite() or rate<=0:
        raise ValueError('Некорректный курс ЦБ')
    stamp=root.attrib.get('Date','')
    rate_date=date.strptime(stamp,'%d.%m.%Y') if hasattr(date,'strptime') else __import__('datetime').datetime.strptime(stamp,'%d.%m.%Y').date()
    if (date.today()-rate_date).days > 7:
        raise ValueError('Курс ЦБ устарел более чем на 7 дней')
    return {'rate':str(rate),'date':rate_date.isoformat(),'source':'https://www.cbr.ru/scripts/XML_daily.asp'}


def calc(month: str, amount: object, cushion: str, rate: str, rate_date: str) -> dict:
    if not isinstance(month,str) or len(month)!=7:
        raise ValueError('Укажите месяц YYYY-MM')
    try:
        year,month_num=map(int,month.split('-'))
        if not 2020<=year<=2100: raise ValueError
        calendar.monthrange(year,month_num)
    except (ValueError,TypeError):
        raise ValueError('Укажите корректный месяц YYYY-MM')
    if cushion not in ('building','funded'): raise ValueError('Некорректный режим подушки')
    fx=Decimal(rate)
    if not fx.is_finite() or fx<=0: raise ValueError('Некорректный курс USD/RUB')
    total=as_minor(amount)
    reserve, invest=split(total,[40,60] if cushion=='building' else [10,90])
    sectors=split(invest,[v for _,v in WEIGHTS])
    foreign=split(sectors[1],[v for _,v in FOREIGN]); crypto=split(sectors[2],[v for _,v in CRYPTO])
    fridays=[day for day in range(1,calendar.monthrange(year,month_num)[1]+1) if date(year,month_num,day).weekday()==4]
    entries=[]
    def add(d:date,category:str,name:str,rub_cents:int):
        usd_cents=int((Decimal(rub_cents)/fx).quantize(Decimal(1),rounding=ROUND_HALF_UP))
        entries.append({'id':f'{d.isoformat()}|{category}|{name}','date':d.isoformat(),'category':category,'asset':name,'rub_cents':rub_cents,'usd_cents':usd_cents,'done':False})
    for (name,_), amount_rub in zip(CRYPTO,crypto):
        if name not in ('BTC','ETH'): add(date(year,month_num,1),'crypto',name,amount_rub)
        else:
            for day,chunk in zip(fridays,split(amount_rub,[1]*len(fridays))): add(date(year,month_num,day),'crypto',name,chunk)
    for (name,_), amount_rub in zip(FOREIGN,foreign):
        for day,chunk in zip(fridays,split(amount_rub,[1]*len(fridays))): add(date(year,month_num,day),'foreign',name,chunk)
    entries.sort(key=lambda r:(r['date'],r['category'],r['asset']))
    wallet=sum(sectors[1:])
    return {'month':month,'amount_cents':total,'cushion':cushion,'reserve_cents':reserve,'invest_cents':invest,
        'sectors':[{'name':k,'rub_cents':v} for (k,_),v in zip(WEIGHTS,sectors)],
        'wallet_rub_cents':wallet,'wallet_usd_cents':int((Decimal(wallet)/fx).quantize(Decimal(1),rounding=ROUND_HALF_UP)),
        'fx_rate':str(fx),'fx_date':rate_date,'schedule':entries}


class MonthlyPlans:
    def __init__(self,db_path:Path):
        self.db_path=Path(db_path)
        self.db_path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS monthly_investment_plans (month TEXT PRIMARY KEY, amount_cents INTEGER NOT NULL, cushion TEXT NOT NULL, fx_rate TEXT NOT NULL, fx_date TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)')
            db.execute("CREATE TABLE IF NOT EXISTS investment_contributions (id INTEGER PRIMARY KEY AUTOINCREMENT, target_month TEXT NOT NULL, amount_cents INTEGER NOT NULL CHECK(amount_cents > 0), received_on TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
            db.execute('CREATE INDEX IF NOT EXISTS idx_investment_contributions_month ON investment_contributions(target_month)')
            db.execute('CREATE TABLE IF NOT EXISTS monthly_investment_purchases (month TEXT NOT NULL, purchase_id TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(month,purchase_id), FOREIGN KEY(month) REFERENCES monthly_investment_plans(month) ON DELETE CASCADE)')
    def connect(self):
        db=sqlite3.connect(self.db_path,timeout=10)
        db.execute('PRAGMA foreign_keys=ON')
        return db
    def get(self,month:str):
        with self.connect() as db:
            row=db.execute('SELECT amount_cents,cushion,fx_rate,fx_date FROM monthly_investment_plans WHERE month=?',(month,)).fetchone()
            if row is None:return None
            plan=calc(month,Decimal(row[0])/100,row[1],row[2],row[3])
            done={r[0] for r in db.execute('SELECT purchase_id FROM monthly_investment_purchases WHERE month=? AND done=1',(month,))}
            for item in plan['schedule']:item['done']=item['id'] in done
            return plan
    def save(self,month:str,amount:object,cushion:str,fx:dict):
        plan=calc(month,amount,cushion,fx['rate'],fx['date'])
        with self.connect() as db:
            old=db.execute('SELECT 1 FROM monthly_investment_plans WHERE month=?',(month,)).fetchone()
            if old: raise ValueError('План на этот месяц уже сохранён. Существующие отметки сохранены; менять план без отдельной операции нельзя.')
            db.execute('INSERT INTO monthly_investment_plans(month,amount_cents,cushion,fx_rate,fx_date) VALUES(?,?,?,?,?)',(month,plan['amount_cents'],cushion,fx['rate'],fx['date']))
        return plan
    def mark(self,month:str,purchase_id:str,done:bool):
        plan=self.get(month)
        if not plan:raise ValueError('План не найден')
        if purchase_id not in {p['id'] for p in plan['schedule']}:raise ValueError('Покупка не найдена в плане')
        with self.connect() as db:
            db.execute('INSERT INTO monthly_investment_purchases(month,purchase_id,done) VALUES(?,?,?) ON CONFLICT(month,purchase_id) DO UPDATE SET done=excluded.done',(month,purchase_id,int(done)))
        return self.get(month)

    def contributions(self, month: str) -> dict:
        # Reuse month validation, but don't call the FX service.
        calc(month, '0', 'building', '1', '2026-01-01')
        with self.connect() as db:
            rows=db.execute('SELECT id, amount_cents, received_on, note FROM investment_contributions WHERE target_month=? ORDER BY received_on,id',(month,)).fetchall()
            locked=db.execute('SELECT 1 FROM monthly_investment_plans WHERE month=?',(month,)).fetchone() is not None
        return {'month':month,'locked':locked,'total_cents':sum(r[1] for r in rows),'items':[{'id':r[0],'amount_cents':r[1],'received_on':r[2],'note':r[3]} for r in rows]}

    def add_contribution(self, month: str, amount: object, received_on: str, note: str = '') -> dict:
        calc(month,'0','building','1','2026-01-01')
        cents=as_minor(amount)
        if cents<=0: raise ValueError('Сумма поступления должна быть больше нуля')
        try: received=date.fromisoformat(received_on)
        except (ValueError,TypeError): raise ValueError('Укажите дату YYYY-MM-DD')
        if received>date.today(): raise ValueError('Дата поступления не может быть в будущем')
        if not isinstance(note,str) or len(note)>200: raise ValueError('Комментарий слишком длинный')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM monthly_investment_plans WHERE month=?',(month,)).fetchone():
                raise ValueError('План закрыт; поступления этого месяца уже распределены')
            db.execute('INSERT INTO investment_contributions(target_month,amount_cents,received_on,note) VALUES(?,?,?,?)',(month,cents,received.isoformat(),note.strip()))
        return self.contributions(month)

    def delete_contribution(self, month: str, contribution_id: int) -> dict:
        calc(month,'0','building','1','2026-01-01')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM monthly_investment_plans WHERE month=?',(month,)).fetchone():
                raise ValueError('Закрытый месяц нельзя изменять')
            row=db.execute('DELETE FROM investment_contributions WHERE target_month=? AND id=? RETURNING id',(month,contribution_id)).fetchone()
            if row is None: raise ValueError('Поступление не найдено')
        return self.contributions(month)

    def close_contributions(self, month: str, cushion: str, fx: dict) -> dict:
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM monthly_investment_plans WHERE month=?',(month,)).fetchone():
                raise ValueError('План на этот месяц уже сохранён')
            amount_cents=db.execute('SELECT COALESCE(SUM(amount_cents),0) FROM investment_contributions WHERE target_month=?',(month,)).fetchone()[0]
            if amount_cents<=0: raise ValueError('Сначала добавьте хотя бы одно поступление')
            plan=calc(month,Decimal(amount_cents)/100,cushion,fx['rate'],fx['date'])
            db.execute('INSERT INTO monthly_investment_plans(month,amount_cents,cushion,fx_rate,fx_date) VALUES(?,?,?,?,?)',(month,amount_cents,cushion,fx['rate'],fx['date']))
        return plan
