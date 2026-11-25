"""Account-scoped long-term portfolio view. Market values, sourced facts, no orders."""
import json
from collections import defaultdict
from datetime import datetime,timezone
from decimal import Decimal
from pathlib import Path
from .portfolio_store import PortfolioStore
from .iis_review import number,percent,day_of,API_SOURCE
from .tinvest import TInvest

TARGETS={'shares':20,'bonds':70,'gold':10}
NAMES={'shares':'Акции','bonds':'Облигации','gold':'Золото','cash':'Деньги','other':'Другие активы'}
GOLD_ISIN='RU000A101X50'
GOLD_SOURCE='https://www.tbank.ru/invest/research/etf/gold/'
READS={('UsersService','GetAccounts'),('OperationsService','GetPortfolio'),('OperationsService','GetOperationsByCursor')} | {
    ('InstrumentsService',m) for m in ('GetInstrumentBy','BondBy','GetBondCoupons','ShareBy','EtfBy','GetAssetFundamentals','GetDividends')}

class ResearchAPI(TInvest):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.session.verify=str(Path(__file__).resolve().parents[1]/'deploy/tbank-ca.pem')
    def call(self,service,method,payload=None):
        if (service,method) not in READS:raise ValueError('Разбор ИИС разрешает только чтение данных.')
        return super().call(service,method,payload)

def prepare(store):
    with store.connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS iis_asset_profiles(uid TEXT PRIMARY KEY,payload TEXT NOT NULL,updated_at TEXT NOT NULL)')

def profiles(store):
    prepare(store)
    with store.connect() as db:return {r['uid']:{'data':json.loads(r['payload']),'updated_at':r['updated_at']} for r in db.execute('SELECT * FROM iis_asset_profiles')}

def collect_profiles(api,store,account,day):
    prepare(store);warnings=[];items={};assets={}
    with store.connect() as db:
        positions=[dict(r) for r in db.execute('SELECT * FROM position_snapshots WHERE account_id=? AND day=?',(account,day.isoformat()))]
    for p in positions:
        kind=p['kind'].lower()
        if kind not in ('share','etf'):continue
        try:
            info=api.call('InstrumentsService','ShareBy' if kind=='share' else 'EtfBy',{'idType':'INSTRUMENT_ID_TYPE_UID','id':p['instrument_id']}).get('instrument',{})
            if not info or info.get('uid')!=p['instrument_id']:raise ValueError('Wrong instrument')
            items[p['instrument_id']]=info
            if kind=='share' and info.get('assetUid'):assets[info['assetUid']]=p['instrument_id']
        except Exception:warnings.append('Не обновлена расширенная карточка '+p['name'])
    if assets:
        try:
            rows=api.call('InstrumentsService','GetAssetFundamentals',{'assets':list(assets)}).get('fundamentals',[])
            seen=set()
            for value in rows:
                ident=value.get('assetUid')
                if ident not in assets or ident in seen:raise ValueError('Ambiguous fundamentals')
                seen.add(ident)
            for value in rows:items[assets[value['assetUid']]]['fundamentals']=value
        except Exception:warnings.append('Фундаментальные показатели акций сейчас недоступны; оценки не подменены догадками.')
    stamp=datetime.now(timezone.utc).isoformat()
    with store.connect() as db:
        for uid,value in items.items():db.execute('INSERT OR REPLACE INTO iis_asset_profiles VALUES(?,?,?)',(uid,json.dumps(value,ensure_ascii=False),stamp))
    return warnings

def classify(p,meta):
    kind=p['kind'].lower();ticker=p['ticker'].rstrip('@').upper()
    if kind in ('share','instrument_type_share'):return 'shares',API_SOURCE
    if kind in ('bond','instrument_type_bond'):return 'bonds',API_SOURCE
    if kind in ('currency','instrument_type_currency'):return 'cash',API_SOURCE
    if kind in ('etf','instrument_type_etf') and (meta.get('isin')==GOLD_ISIN or ticker=='TGLD'):
        return 'gold',GOLD_SOURCE
    return 'other',API_SOURCE

def view(store,account,review,*,today,evidence=None,references=None):
    evidence=evidence or {};references=references or {};saved=profiles(store)
    with store.connect() as db:
        raw=db.execute('SELECT * FROM portfolio_snapshots WHERE account_id=? ORDER BY day DESC LIMIT 1',(account,)).fetchone()
        if not raw:return {'has_data':False}
        snap=dict(raw);positions=[dict(r) for r in db.execute('SELECT * FROM position_snapshots WHERE account_id=? AND day=?',(account,snap['day']))]
    total=number(snap['total_rub']) or Decimal(0);stale=not 0<=(today-day_of(snap['day'])).days<=3
    bonds={r['id']:r for r in review.get('bonds',[])};rows=[];sums=defaultdict(lambda:Decimal(0));groups={};unknown=0
    for p in positions:
        uid=p['instrument_id'];qty=number(p['quantity']);price=number(p['price']);kind=p['kind'].lower()
        profile=saved.get(uid,{});meta=profile.get('data',{});bond=bonds.get(uid);extra=evidence.get(uid,{})
        asset_class,source=classify(p,meta)
        value=None;gaps=[];reasons=[];facts=[];fundamentals=[];observations=[]
        if p['currency']=='RUB' and qty is not None and price is not None:
            if kind=='bond':
                nkd=number(p['nkd'])
                if nkd is not None:value=qty*(price+nkd)
                else:gaps.append('Нет НКД: полная рыночная стоимость не подтверждена.')
            else:value=qty*price
        if value is None:unknown+=1;gaps.append('Нет подтверждённой рублёвой оценки; курс валюты не подставляется.')
        else:sums[asset_class]+=value
        if qty is None or qty<0:gaps.append('Короткая или некорректная позиция требует отдельной проверки.')
        pct=percent(value,total) if value is not None else None
        issuer_name=None;issuer_id=None;issuer_source=None
        if bond:
            issuer_name=bond['issuer_name'];issuer_id=bond['issuer_id'];issuer_source=bond['issuer_source']
            reasons += [('По номиналу облигационной части: ' if s.startswith(('Доля эмитента','Превышен лимит')) else '')+s for s in bond['reasons']]
            gaps+=bond['gaps']
            facts += [('Риск',bond['risk_label']+' · '+bond['risk_source']),('Погашение',bond.get('maturity') or 'Не подтверждено'),
                      ('Купон меняется','Да' if bond.get('floating') is True else 'Нет' if bond.get('floating') is False else 'Не подтверждено'),
                      ('Возврат частями','Да' if bond.get('amortizing') is True else 'Нет' if bond.get('amortizing') is False else 'Не подтверждено')]
            for rating in bond.get('ratings',[]):
                facts.append(('Рейтинг выпуска',f"{rating.get('level') or '—'} · {rating.get('agency') or 'агентство не указано'} · {rating.get('date') or 'без даты'}"+(' · не используется' if not rating.get('usable') else '')))
            observations.append('Для долгосрочного удержания важны способность эмитента обслуживать долг, срок погашения и условия оферты, а не только размер купона.')
            if bond.get('floating') is True:observations.append('Плавающий купон: доход может снизиться вслед за базовой ставкой. Текущую выплату нельзя обещать на весь срок.')
            elif bond.get('floating') is False:observations.append('Фиксированный купон: при росте ставок рыночная цена может снижаться, даже если выплаты идут по плану.')
            if bond.get('amortizing'):observations.append('Амортизация возвращает часть номинала раньше погашения; последующие купоны могут уменьшаться вместе с остатком долга.')
        else:
            ref=references.get(meta.get('isin'),{})
            issuer_id=extra.get('issuer_id') or ref.get('issuer_id') or ('asset:'+meta['assetUid'] if meta.get('assetUid') and asset_class=='shares' else None)
            issuer_name=extra.get('issuer_name') or ref.get('issuer_name') or (p['name'] if asset_class=='shares' else None)
            issuer_source=extra.get('source_url') or ref.get('source_url') or (API_SOURCE if issuer_id else None)
            age=(today-day_of(profile.get('updated_at'))).days if day_of(profile.get('updated_at')) else None
            if asset_class=='shares':
                if age is None or not 0<=age<=7:gaps.append('Карточка компании требует обновления.')
                f=meta.get('fundamentals',{});currency=f.get('currency')
                for key,label in [('revenueTtm','Выручка за 12 месяцев'),('netIncomeTtm','Прибыль за 12 месяцев'),('freeCashFlowTtm','Свободный денежный поток'),('peRatioTtm','Цена / прибыль'),('netDebtToEbitda','Чистый долг / EBITDA')]:
                    v=number(f.get(key))
                    if v is not None and v!=0:fundamentals.append({'key':key,'label':label,'value':float(v),'currency':currency if key in ('revenueTtm','netIncomeTtm','freeCashFlowTtm') else None})
                profit=number(f.get('netIncomeTtm'))
                if profit is None or profit==0:gaps.append('Прибыль компании не подтверждена ненулевым показателем API; одной оценки P/E недостаточно.')
                if profit is not None and profit<0:reasons.append('API показывает убыток за последние 12 месяцев: пересмотреть инвестиционный тезис.')
                elif profit is not None and profit>0:observations.append('Компания прибыльна по показателю за 12 месяцев из API. Это поддерживает тезис удержания, но не гарантирует будущих дивидендов.')
                cash_flow=number(f.get('freeCashFlowTtm'))
                if cash_flow is not None and cash_flow<0:observations.append('Свободный денежный поток отрицательный. Нужно сверить инвестиционные расходы и источники финансирования; само по себе это ещё не основание продать.')
                elif cash_flow is not None and cash_flow>0:observations.append('Свободный денежный поток положительный: после расходов у компании остаются деньги. Устойчивость проверяется по нескольким периодам.')
                debt=number(f.get('netDebtToEbitda'))
                if meta.get('sector') not in ('financial','finance') and debt is not None and debt>4:reasons.append('Чистый долг / EBITDA выше 4: долговая нагрузка требует проверки.')
                if meta.get('sector') in ('financial','finance'):observations.append('Для финансовой компании долг / EBITDA не служит обычным фильтром: отдельно нужны достаточность капитала и качество активов.')
                if not f or not fundamentals:gaps.append('Нет достаточных фундаментальных показателей компании.')
                facts.append(('Сектор',meta.get('sector') or 'Не подтверждён'))
                facts.append(('Данные компании',profile.get('updated_at') or 'Не загружены'))
                gaps.append('API не указывает дату окончания отчётного периода; показатели не заменяют чтение отчётности.')
            elif asset_class=='gold':
                facts.append(('Роль','Золотая часть долгосрочного портфеля'))
                facts.append(('Основа классификации','Подтверждённый фонд TGLD, не акции золотодобытчика'))
                observations.append('Золото диверсифицирует портфель, но не платит купоны. Роль — целевые 10%, а не замена облигационного дохода.')
            elif asset_class=='other':gaps.append('Фонд или другой актив не приравнен автоматически к облигациям либо золоту.')
        if issuer_id:
            group=groups.setdefault(issuer_id,{'id':issuer_id,'name':issuer_name or issuer_id,'value':Decimal(0),'count':0,'source':issuer_source})
            group['count']+=1
            if value is not None:group['value']+=value
        rows.append({'id':uid,'name':p['name'],'ticker':p['ticker'],'kind':kind,'class':asset_class,'class_label':NAMES[asset_class],
            'quantity':p['quantity'],'price':p['price'],'currency':p['currency'],'market_value':float(value) if value is not None else None,'weight_pct':pct,
            'issuer_id':issuer_id,'issuer_name':issuer_name,'source':source,'issuer_source':issuer_source,'facts':[{'label':k,'value':v} for k,v in facts],
            'fundamentals':fundamentals,'observations':observations,'reasons':reasons,'gaps':list(dict.fromkeys(gaps)),
            'evidence':extra,'bond_risk':bond.get('risk') if bond else None})
    # Broker class totals include FX-valued positions. Do not renormalize away cash or unknown funds.
    values={'shares':number(snap['shares_rub']) or Decimal(0),'bonds':number(snap['bonds_rub']) or Decimal(0),
            'gold':sums['gold'],'cash':number(snap['cash_rub']) or Decimal(0)}
    values['other']=total-sum(values.values(),Decimal(0))
    allocation=[]
    for k in NAMES:
        target=TARGETS.get(k);v=values[k];weight=percent(v,total)
        allocation.append({'key':k,'label':NAMES[k],'value':float(v),'weight_pct':weight,'target_pct':target,
            'delta_pp':round(weight-target,2) if weight is not None and target is not None else None,
            'target_value':float(total*Decimal(target)/100) if target is not None else None,
            'gap_value':float(total*Decimal(target)/100-v) if target is not None else None})
    alloc={r['key']:r for r in allocation};issuer_limit=review.get('policy',{}).get('issuer_limit_pct',15)
    for r in rows:
        group=groups.get(r['issuer_id']);group_pct=percent(group['value'],total) if group else None
        if group_pct is not None and group_pct>issuer_limit:r['reasons'].append(f'На эмитента приходится {group_pct}% всего счёта при лимите {issuer_limit}%.')
        deviation=alloc[r['class']]['delta_pp']
        if deviation is not None and deviation>5:r['reasons'].append('Класс активов выше целевой доли более чем на 5 п.п.; не наращивать без ребалансировки.')
        if stale or r['market_value'] is None or total<=0 or (number(r['quantity']) or Decimal(0))<0:
            action='verify';label='Обновить данные';why='Сначала получить актуальную оценку. Решение о продаже по старому снимку не делается.'
        elif r['reasons'] or r['bond_risk']=='high':
            action='reduce';label='Рассмотреть сокращение';why='Есть конкретные факторы для пересмотра. Сначала сверить причины; при отклонении долей можно использовать новые пополнения вместо продажи.'
        elif (r['class']=='bonds' and r['gaps']) or (r['class']=='shares' and (not r['fundamentals'] or len(r['gaps'])>1)) or r['class']=='other':
            action='verify';label='Проверить перед решением';why='Данных недостаточно для уверенного вывода о компании или выпуске. Пробел не является самостоятельной причиной продажи.'
        else:
            action='hold';label='Держать под наблюдением';why='По доступным данным явных нарушений цели не найдено. Это условный вывод: проверять компанию, условия и концентрацию при новых данных.'
        r.update(action=action,action_label=label,conclusion=why)
    priorities=sorted((r for r in allocation if r.get('gap_value',0) and r['gap_value']>0),key=lambda r:-r['gap_value']) if not stale and total>0 and not unknown else []
    mismatch=total-sum((Decimal(str(r['market_value'])) for r in rows if r['market_value'] is not None),Decimal(0))
    return {'has_data':True,'total':float(total),'as_of':snap['captured_at'],'stale':stale,'targets':TARGETS,'allocation':allocation,'assets':rows,
        'companies':[{'id':g['id'],'name':g['name'],'value':float(g['value']),'weight_pct':percent(g['value'],total),'count':g['count'],'source':g['source']} for g in sorted(groups.values(),key=lambda g:-g['value'])],
        'counts':{'assets':len(rows),'hold':sum(r['action']=='hold' for r in rows),'reduce':sum(r['action']=='reduce' for r in rows),'verify':sum(r['action']=='verify' for r in rows)},
        'rebalance':priorities,'cash':float(values['cash']),'unvalued_count':unknown,'reconciliation_gap':float(mismatch),
        'method':['Цель: 20% акций, 70% облигаций, 10% золота. Доли от полной стоимости выбранного счёта, включая деньги и другие активы.',
                  'Стоимость облигации: количество × (цена из GetPortfolio + НКД). Котировки GetLastPrices в процентах номинала здесь не используются.',
                  'Золотодобывающие компании относятся к акциям. Другие фонды не считаются облигациями или золотом без подтверждения.',
                  'Выводы — прозрачные правила по составу, рискам и данным API; это не автоматические заявки. Компания с пробелами в данных не объявляется надёжной.',
                  'Нулевые фундаментальные поля не считаются доказательством отсутствия долга. Дата загрузки API не является датой отчётности.']}
