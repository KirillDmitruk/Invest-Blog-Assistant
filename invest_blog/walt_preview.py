"""Verified action echo and explicit, conservative pre-fill margin estimates.

An estimated margin boundary is never presented as a broker liquidation quote.
Reference: https://help.walt.io/article/634-trade-and-trading-assets-in-wallet
"""
from decimal import Decimal,InvalidOperation,ROUND_FLOOR,ROUND_CEILING
import hashlib,json


def dec(value):
    if isinstance(value,bool):raise ValueError('Некорректное число предпросмотра Walt.')
    try:
        value=Decimal(str(value))
        if not value.is_finite():raise ValueError
        return value
    except (ValueError,InvalidOperation,TypeError):raise ValueError('Некорректное число предпросмотра Walt.') from None


def alias(value,*keys):
    present=[key for key in keys if key in value]
    if len(present)!=1:raise ValueError('Walt вернул неоднозначные или неполные параметры действия.')
    return value[present[0]]


def normalize_action(action):
    if not isinstance(action,dict) or action.get('kind')!='order.place':raise ValueError('Walt подтвердил другое действие вместо открытия позиции.')
    request=alias(action,'request','params')
    if not isinstance(request,dict):raise ValueError('Walt не подтвердил параметры заявки.')
    allowed={'contract_id','contractId','side','type','leverage','amount','price','auto_close','autoClose','account_type','accountType'}
    if set(request)-allowed:raise ValueError('В действии Walt есть неизвестные параметры. Исполнение остановлено.')
    if {'account_type','accountType'} & set(request):
        if alias(request,'account_type','accountType')!='bot_regular':raise ValueError('Walt подтвердил другой тип счёта.')
    close=alias(request,'auto_close','autoClose')
    if not isinstance(close,dict) or set(close)-{'take_profit','takeProfit','stop_loss','stopLoss'}:raise ValueError('Walt не подтвердил защитные уровни.')
    result={key:request.get(key) for key in ('side','type','leverage','amount','price')}
    result['contractId']=alias(request,'contract_id','contractId');result['autoClose']={}
    for snake,camel in (('take_profit','takeProfit'),('stop_loss','stopLoss')):
        leg=alias(close,snake,camel)
        if not isinstance(leg,dict) or set(leg)-{'price_quote','priceQuote'}:raise ValueError('Неизвестный формат защитного уровня Walt.')
        result['autoClose'][camel]={'priceQuote':alias(leg,'price_quote','priceQuote')}
    return result


def verify_action(action,expected):
    actual=normalize_action(action)
    for key in ('side','type'):
        if actual[key]!=expected[key]:raise ValueError('Направление или тип заявки в ответе Walt отличается от расчёта.')
    for key in ('contractId','leverage','amount','price'):
        if dec(actual[key])!=dec(expected[key]):raise ValueError('Контракт, плечо, сумма или цена в ответе Walt отличается от расчёта.')
    for leg in ('takeProfit','stopLoss'):
        if dec(actual['autoClose'][leg]['priceQuote'])!=dec(expected['autoClose'][leg]['priceQuote']):raise ValueError('Стоп или тейк в ответе Walt отличается от расчёта.')


def verify_risk(risk):
    if not isinstance(risk,dict) or risk.get('ok') is not True or not isinstance(risk.get('checks'),list):
        raise ValueError('Walt не подтвердил предварительные проверки риска.')
    checks=risk['checks']
    if any(not isinstance(c,dict) or c.get('ok') is not True for c in checks):raise ValueError('Одна из проверок Walt не пройдена.')
    if not {'contract_active','leverage','amount','price'}.issubset({c.get('name') for c in checks}):raise ValueError('Walt не вернул обязательные проверки контракта, плеча, суммы и цены.')


def contract_fingerprint(contract):
    fields={key:contract.get(key) for key in ('tick_size','step_size','price_slippage','taker_fee_pct','max_leverage','margin')}
    return hashlib.sha256(json.dumps(fields,sort_keys=True).encode()).hexdigest()


def conservative_boundary(proposal,contract):
    direction=1 if proposal['side']=='long' else -1
    price=dec(proposal['reference_price']);amount=dec(proposal['margin']);leverage=dec(proposal['leverage'])
    tick=dec(contract.get('tick_size'));step=dec(contract.get('step_size'))
    fee=dec(contract.get('taker_fee_pct'));slip=dec(contract.get('price_slippage'));maximum=dec(contract.get('max_leverage'))
    margin=contract.get('margin')
    if not isinstance(margin,dict):raise ValueError('Walt не вернул параметры маржи для оценки защиты.')
    initial=dec(margin.get('min_initial_margin_fraction'));maintenance=dec(margin.get('maintenance_margin_fraction'));closeout=dec(margin.get('closeout_margin_fraction'))
    # Validate both the fraction scale and hierarchy against max leverage. Do not
    # guess units from a naked maintenance number. Reserve INITIAL margin (larger
    # than maintenance), plus 1% liquidation fee and a closing fee as extra buffer.
    if not (0<closeout<=maintenance<=initial<10000) or maximum<1 or abs(initial*maximum-10000)>maximum:
        raise ValueError('Единицы или соотношение параметров маржи Walt не подтверждены.')
    if min(price,amount,tick,step)>0 and 1<=leverage<=5 and leverage<=maximum and 0<=fee<Decimal('.1') and 0<=slip<Decimal('.1'):pass
    else:raise ValueError('Недостаточно подтверждённых параметров для оценки защиты.')
    entry=((price*(1+direction*slip)/tick).to_integral_value(rounding=ROUND_CEILING if direction==1 else ROUND_FLOOR))*tick
    notional=amount/(1/leverage+fee)
    quantity=(notional/entry/step).to_integral_value(rounding=ROUND_FLOOR)*step
    if quantity<=0:raise ValueError('Недостаточное количество контракта для оценки защиты.')
    funding_price=max(price,entry,dec(proposal['stop'])*(1-direction*slip),dec(proposal['take'])*(1-direction*slip))
    funding=max(Decimal(0),dec(contract.get('funding_rate_hourly'))*direction)*24*quantity*funding_price
    # Do not assume the unspent remainder from quantity rounding becomes margin.
    net_margin=min(amount-quantity*entry*fee,quantity*entry/leverage)-funding
    reserve=max(initial,maintenance)/10000+Decimal('.01')+fee
    if net_margin<=0 or not 0<reserve<1:raise ValueError('Маржи недостаточно с учётом резервов расходов.')
    boundary=(entry-direction*net_margin/quantity)/(1-direction*reserve)
    boundary=max(Decimal(0),boundary) if direction==1 else boundary
    boundary=(boundary/tick).to_integral_value(rounding=ROUND_CEILING if direction==1 else ROUND_FLOOR)*tick
    return {'price':format(boundary,'f'),'source':'local_conservative_estimate','entry_bound':format(entry,'f'),
        'quantity':format(quantity,'f'),'reserved_margin_fraction':format(reserve,'f'),
        'funding_24h_reserve':format(funding,'f'),'contract_fingerprint':contract_fingerprint(contract),
        'note':'Walt не вернул цену ликвидации. Это расчётная консервативная граница, не котировка и не гарантия Walt. '
               'Учтены неблагоприятный вход, резерв начальной маржи, 1% комиссии ликвидации, комиссии и 24ч текущего платного фандинга без зачёта поступлений. '
               'Фактическая ликвидация и фандинг могут отличаться; после исполнения нужна сверка позиции.'}


def protection_evidence(proposal,contract,risk):
    found=[]
    for container in [risk]+[risk.get(k) for k in ('position','estimates','preview') if isinstance(risk.get(k),dict)]:
        for key in ('liquidation_price','liquidationPrice'):
            if container.get(key) is not None:found.append(dec(container[key]))
    if found:
        if any(value<=0 or value!=found[0] for value in found):raise ValueError('Walt вернул некорректные или противоречивые цены ликвидации.')
        evidence={'price':format(found[0],'f'),'source':'provider','contract_fingerprint':contract_fingerprint(contract),
            'note':'Оценка ликвидации из предпросмотра Walt; фактическая цена исполнения может отличаться.'}
    else:evidence=conservative_boundary(proposal,contract)
    direction=1 if proposal['side']=='long' else -1
    stop=dec(proposal['stop']);price=dec(proposal['reference_price']);slip=dec(contract.get('price_slippage'))
    stop_fill=stop*(1-direction*slip)
    buffer=max(price*Decimal('.01'),2*dec(contract['tick_size']))
    evidence['stop_fill_estimate']=format(stop_fill,'f');evidence['buffer']=format(buffer,'f')
    evidence['safe']=direction*(stop_fill-dec(evidence['price']))>buffer
    return evidence
