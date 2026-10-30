"""v1.4: local, deterministic content suggestions based on verified holdings and editorial history.

Suggestions are ideas, not financial recommendations or AI-generated financial claims.
"""
from __future__ import annotations

from collections import Counter
from decimal import Decimal, InvalidOperation
import json
from datetime import date, datetime, timedelta

from .posts import TERMS, current_week, previous_month, previous_workweek
from .publication_schedule import LABELS, WEEKLY_RULES

RUBRICS = ('weekly', 'monthly', 'plan', 'paper', 'glossary', 'purchase', 'placement')



def period_for(kind: str, day: date) -> str:
    if kind == 'monthly':
        first, _ = previous_month(day)
        return first.strftime('%Y-%m')
    if kind == 'weekly':
        first, _ = previous_workweek(day)
        return first.isoformat()
    if kind in ('paper', 'glossary', 'plan'):
        return current_week(day)[0].isoformat()
    return day.isoformat()


class ContentPlanner:
    def __init__(self, portfolio, drafts):
        self.portfolio = portfolio
        self.drafts = drafts

    def paper_candidates(self, limit: int = 5) -> list[dict]:
        latest = self.portfolio.latest_day()
        if not latest:
            return []
        next_month=(date.fromisoformat(latest)+timedelta(days=35)).isoformat()
        with self.portfolio.connect() as db:
            rows = db.execute('''SELECT instrument_id,name,ticker,kind,quantity,price,currency
                FROM position_snapshots WHERE day=? AND kind IN ('bond','share')
                AND CAST(quantity AS REAL)>0''', (latest,)).fetchall()
            meta = {r['instrument_id']:json.loads(r['raw_json']) for r in db.execute(
                'SELECT instrument_id,raw_json FROM bond_metadata')}
            upcoming={r['instrument_id'] for r in db.execute('''SELECT DISTINCT instrument_id
                 FROM bond_coupon_events WHERE coupon_date BETWEEN ? AND ?''',(latest,next_month))}
            snapshot_sum=db.execute('SELECT SUM(CAST(total_rub AS REAL)) AS total FROM portfolio_snapshots WHERE day=?',(latest,)).fetchone()['total']
            recent_buy = {r['instrument_id'] for r in db.execute('''SELECT DISTINCT instrument_id FROM broker_operations
                 WHERE kind LIKE 'OPERATION_TYPE_BUY%' AND executed_at>=?
                 AND instrument_id IS NOT NULL''', ((date.fromisoformat(latest)-timedelta(days=45)).isoformat(),))}
            topics = [r['topic_key'] for r in db.execute('''SELECT topic_key FROM editorial_topics
                 WHERE kind='paper' ORDER BY id DESC LIMIT 40''')]
        counts = Counter(topics)
        grouped={}
        for row in rows:
            uid=row['instrument_id']
            entry=grouped.setdefault(uid,{'instrument_id':uid,'name':row['name'],
                            'ticker':row['ticker'],'kind':row['kind'], 'estimated_value':Decimal(0),
                            'value_known':True})
            try:
                qty=Decimal(row['quantity']); price=Decimal(row['price'])
                if qty<=0 or price<=0:
                    raise ValueError('Нет цены')
                currency=(row['currency'] or '').upper()
                if row['kind']=='share' and currency=='RUB':
                    amount=qty*price
                elif row['kind']=='bond' and currency in ('PT.', 'PT', 'RUB'):
                    if currency=='RUB':
                        amount=qty*price
                    else:
                        nominal=meta.get(uid,{}).get('nominal') or {}
                        if str(nominal.get('currency') or '').lower() not in ('rub','rur'):
                            raise ValueError('Номинал не RUB')
                        n=Decimal(str(nominal.get('units') or 0))+Decimal(str(nominal.get('nano') or 0))/Decimal('1000000000')
                        if n<=0:raise ValueError('Нет номинала')
                        amount=qty*price*n/Decimal(100)
                else:
                    raise ValueError('Валюта неизвестна')
                entry['estimated_value']+=amount
            except (InvalidOperation,ValueError,TypeError):
                entry['value_known']=False
        total=Decimal(str(snapshot_sum or 0))
        results=[]
        for uid,entry in grouped.items():
            last_position=topics.index(uid) if uid in topics else None
            recently_bought=uid in recent_buy
            soon_coupon=uid in upcoming
            share=(entry['estimated_value']/total*100) if total>0 and entry['value_known'] else None
            notable_share=share is not None and share>=10
            score=(30 if last_position is None else min(last_position,20)) + (15 if recently_bought else 0) + (8 if soon_coupon else 0) + (5 if notable_share else 0) - counts[uid]*3
            reasons=[]
            if recently_bought:reasons.append('недавняя покупка')
            if soon_coupon:reasons.append('ожидается купон в ближайшие 35 дней')
            if notable_share:reasons.append('заметная доля портфеля (оценка)')
            reasons.append('ранее не разбиралась' if last_position is None else 'тема уже использовалась')
            results.append({'instrument_id':uid,'name':entry['name'],'ticker':entry['ticker'],
                            'kind':entry['kind'],'recent_buy':recently_bought,
                            'upcoming_coupon':soon_coupon,
                            'published_count':counts[uid], 'reason':'; '.join(reasons),'score':score})
        return sorted(results,key=lambda p:(-p['score'],p['name'],p['instrument_id']))[:limit]

    def glossary_suggestions(self, limit: int = 5) -> list[dict]:
        with self.portfolio.connect() as db:
            topics = [r['topic_key'] for r in db.execute('''SELECT topic_key FROM editorial_topics
                WHERE kind='glossary' ORDER BY id DESC LIMIT 50''')]
        scores = Counter(topics)
        options=[]
        for index, (title, _) in enumerate(TERMS):
            recently = title in topics[:6]
            options.append({'title': title, 'seen': scores[title], 'recently_used': recently,
                            'priority': scores[title] + (100 if recently else 0), 'index':index})
        return sorted(options,key=lambda x:(x['priority'],x['index']))[:limit]

    def calendar(self, today: date | None = None, days: int = 35) -> list[dict]:
        today = today or date.today()
        if days < 1 or days > 93:
            raise ValueError('Период контент-плана: 1–93 дня')
        first = today
        end = first + timedelta(days=days - 1)
        with self.drafts._connect() as db:
            existing = [dict(r) for r in db.execute('SELECT id,kind,period,state FROM drafts ORDER BY id DESC LIMIT 1500')]
        by_key = {}
        for row in existing:
            by_key.setdefault((row['kind'],row['period']),row)
        items=[]
        cur=first
        while cur<=end:
            kinds=[]
            kinds.extend(kind for kind, weekday, _ in WEEKLY_RULES if weekday == cur.weekday())
            if cur.day==1:kinds.append('monthly')
            for kind in kinds:
                period = period_for(kind,cur)
                match = by_key.get((kind,period))
                items.append({'day':cur.isoformat(),'kind':kind,'label':LABELS[kind],
                              'period':period,'draft_id':match['id'] if match else None,
                              'state':match['state'] if match else 'planned'})
            cur+=timedelta(days=1)
        return items

    def overview(self, today: date | None = None) -> dict:
        paper = self.paper_candidates()
        glossary = self.glossary_suggestions()
        return {
            'schedule':self.calendar(today),
            'paper_candidates':paper,
            'glossary_suggestions':glossary,
            'suggested_paper':paper[0] if paper else None,
            'suggested_term':glossary[0] if glossary else None,
            'notice':'Предложения построены на снимке позиций, истории покупок и тем. Они не являются оценкой доходности или инвестиционными рекомендациями.'
        }
