"""Closed-bar scenario evidence and useful reports with no entry candidates."""
from copy import deepcopy
import pytest
from invest_blog.cloud_scenarios import closed_four_hour,describe,divergence,focus
from invest_blog.cloud_signals import review,report,price_text
from invest_blog.walt_history import HOUR
from test_cloud_signals import packet,Session


def bars(direction=1,length=480):
    result=[]
    for i in range(length):
        c=100+direction*i*.05
        result.append({'t':i*HOUR,'o':c-direction*.05,'h':c+.1,'l':c-.1,'c':c,'v':100,'V':100000})
    return result


def test_four_hour_bars_exclude_incomplete_groups_and_use_actual_ohlcv():
    b=bars(length=10);four=closed_four_hour(b)
    assert len(four)==2 and four[-1]['t']==4*HOUR
    assert four[0]['o']==b[0]['o'] and four[0]['c']==b[3]['c']
    assert four[0]['h']==max(x['h'] for x in b[:4]) and four[0]['V']==400000
    assert len(closed_four_hour(b[:2]+b[3:]))==1


@pytest.mark.parametrize('direction,code',[(1,'up_continuation'),(-1,'down_continuation')])
def test_trend_continuation_is_separate_from_overbought_or_oversold_warning(direction,code):
    b=bars(direction);s=describe(b)
    assert s['code']==code and s['aligned']
    assert s['flags']  # RSI saturation is a warning, not a claimed reversal.
    assert s['conditions']['confirmation']>0 and s['conditions']['invalidation']>0
    assert 'change_24h_pct' not in s['metrics_4h']  # 24 x 4h is not a daily return.


def test_range_does_not_produce_a_direction_or_probability():
    b=bars()
    for r in b:r.update(o=100,h=101,l=99,c=100)
    s=describe(b)
    assert s['code']=='range' and not s['aligned']
    assert s['support']==99 and s['resistance']==101
    assert 'probability' not in s and 'confidence_pct' not in s


def test_rebound_does_not_claim_old_downtrend_has_reversed():
    b=bars(-1)
    for i in range(8):
        c=b[-9]['c']+.2*(i+1);b[-8+i].update(o=c-.1,c=c,h=c+.1,l=c-.1)
    s=describe(b)
    assert s['trend_4h']=='down'
    assert s['code']=='rebound' and 'не путать' in s['advice'].lower()


def test_breakout_needs_volume_and_does_not_confirm_a_reversal():
    b=bars(-1);last=b[-1];last.update(c=90,h=90.1,o=76,l=75,V=100000)
    without=describe(b)
    last['V']=200000
    with_volume=describe(b)
    assert without['code']!='reversal_up_watch'
    assert with_volume['code']=='reversal_up_watch'
    assert 'ещё не доказывают' in with_volume['advice']
    assert with_volume['trend_4h']!='up'


def test_support_resistance_use_only_prior_bars():
    b=bars();expected=min(x['l'] for x in b[-25:-1]);b[-1]['l']=1
    assert describe(b)['support']==expected


def test_pivots_require_two_closed_right_bars():
    b=bars(length=80);series=[{'rsi14':40} for _ in b]
    b[-10]['l']=1;b[-1]['l']=.5;series[-1]['rsi14']=60
    assert divergence(b,series)['bullish'] is False


def test_gaps_and_insufficient_history_are_not_invented():
    with pytest.raises(ValueError):describe(bars(length=120))
    b=bars();b.pop(50)
    with pytest.raises(ValueError):describe(b)


def test_no_entry_still_calls_ai_for_observation_and_cannot_become_a_trade():
    p=packet();p['rows'][0]['side']='wait';p['rows'][0]['scenario']=describe(bars(-1))
    s=Session({'reviews':[{'market_id':0,'accept':False,'reason':'trend_unclear'}]})
    review(p,'a'*32,'SECRET',s)
    assert s.calls and p['ai']['status']=='ok'
    text=report(p)
    assert 'МСК' in text and '2026-11-21T' not in text
    assert 'снижение сохраняется' in text.lower() and 'Подтверждённых входов сейчас нет' in text
    assert 'Ждать закрытия 4ч ниже' in text and 'сценарий ослабнет' in text
    assert 'кандидатов по правилам: 0' in text and 'Разворот не гарантирован' in text
    p=packet();p['rows'][0]['side']='wait'
    review(p,'a'*32,'SECRET',Session({'reviews':[{'market_id':0,'accept':True,'reason':'checks_pass'}]}))
    assert p['ai']['status']=='unavailable' and 'ai_review' not in p['rows'][0]


def test_news_risk_requires_a_real_supplied_news_reference():
    p=packet();review(p,'a'*32,'SECRET',Session({'reviews':[{'market_id':0,'accept':False,'reason':'news_risk','news_ids':['fake']}]}))
    assert p['ai']['status']=='unavailable'


def test_focus_includes_anchors_without_duplicates_and_caps_size():
    rows=[{**packet()['rows'][0],'market_id':i,'symbol':symbol} for i,symbol in enumerate(['AAA','BBB','NVDA','ETH','BTC','SOL','CCC','DDD','EEE'])]
    chosen=focus(rows)
    assert [r['symbol'] for r in chosen[:4]]==['BTC','ETH','SOL','NVDA']
    assert len(chosen)==8 and len({r['market_id'] for r in chosen})==8


def test_message_is_bounded_and_safety_footer_survives():
    p=packet();p['rows']=[{**deepcopy(p['rows'][0]),'market_id':i,'symbol':'ASSET'+str(i),'scenario':describe(bars(-1))} for i in range(210)]
    p['checked']=p['total']=210
    text=report(p)
    assert len(text)<=3950 and text.endswith('ручное подтверждение.')
    assert text.count('Опора ')<=5
    assert price_text(.00000000001)!='0' and price_text(62000)=='62 000'
