"""Shared, editable voice for weekly/monthly drafts; values come from the ledger."""
from decimal import Decimal
from .model import fmt_money,fmt_num,fmt_totals

CAPITAL_TARGET=Decimal('200000')
MONTHLY_INCOME_TARGET=Decimal('1000')
POST_OPENERS={
    'plan':'Всем привет! ✌️ Заглядываю в календарь: какие выплаты и события могут принести следующие дни?',
    'paper':'Всем привет! ✌️ Сегодня поближе знакомлюсь с одной из бумаг моего портфеля.',
    'glossary':'Всем привет! ✌️ Ещё один небольшой шаг к пониманию инвестиций — разберём термин на простом примере.',
    'purchase':'Всем привет! ✌️ Делюсь последней покупкой и её цифрами.',
}

def milestone(value,target,suffix):
    return ('✅' if value>=target else '🎯')+' '+fmt_num(target,0)+' '+suffix

def render_report(*,start,end,monthly,payments,totals,capital,positions,month_income,purchase_count,purchase_totals,summary):
    period='месяц' if monthly else 'неделю'
    intro='Купоны капают. Маленькими шагами к большим цифрам! 💸' if any(v>0 for v in totals['Купоны'].values()) else 'Маленькими шагами к большим цифрам! 💸'
    lines=[intro,'','Всем привет! ✌️',f'Подвожу итоги за {period} {start:%d.%m}–{end:%d.%m.%Y} 🫡','',
        '🎯 Мои цели:',f'Капитал: {fmt_num(CAPITAL_TARGET,0)} ₽',f'Пассивный доход: {fmt_num(MONTHLY_INCOME_TARGET,0)} ₽/мес.']
    for title,icon in [('Купоны','💰'),('Дивиденды','🏦'),('Погашения','💸')]:
        if title!='Купоны' and not payments[title]:continue
        lines.extend(['',f'{icon} {title} за {period}:',*(payments[title] or ['В этом периоде выплат не было.'])])
    lines.extend(['',f'💵 Всего купонов: {fmt_totals(totals["Купоны"])}'])
    if totals['Дивиденды']:lines.append('🏦 Дивиденды: '+fmt_totals(totals['Дивиденды']))
    if totals['Погашения']:lines.append('💸 Погашения: '+fmt_totals(totals['Погашения']))
    lines.append('➕ Пополнения: '+fmt_totals(totals['Пополнения']))
    inflows={}
    for pool in totals.values():
        for currency,amount in pool.items():inflows[currency]=inflows.get(currency,Decimal(0))+amount
    lines.append('📈 Итого поступлений: '+fmt_totals(inflows))
    if totals['Погашения']:lines.append('Погашения здесь — возврат вложенного номинала, а не заработок.')
    lines.extend(['','♻️ Мой подход — реинвестировать купоны. Даже небольшие суммы могут стать основой будущих выплат.','',
        '🎯 Прогресс по целям:',f'💼 Стоимость портфеля на сегодня: {fmt_money(capital,"RUB")}',
        *[milestone(capital,CAPITAL_TARGET*f,'₽') for f in (Decimal('.5'),Decimal('.75'),Decimal(1))],
        '',f'💸 Купоны и дивиденды за {end:%m.%Y} по {end:%d.%m}: {fmt_money(month_income,"RUB")}',
        *[milestone(month_income,MONTHLY_INCOME_TARGET*f,'₽ за месяц') for f in (Decimal('.5'),Decimal('.75'),Decimal(1))]])
    if monthly:
        lines.extend(['','🛒 Покупки за месяц',f'Количество операций — {purchase_count}',
            'Сумма списаний — '+fmt_totals(purchase_totals) if purchase_count else 'Покупок не было',
            f'Активных позиций (облигации и акции): {positions}'])
    lines.extend(['','🤔 '+summary,'',
        'Капитал — текущий снимок, не доходность за период. Галочки по выплатам — фактические поступления в рублях за указанный месяц, не прогноз.',
        'Суммы по исполненным операциям брокера; налоги и комиссии отдельно не вычитались.'])
    return '\n'.join(lines)
