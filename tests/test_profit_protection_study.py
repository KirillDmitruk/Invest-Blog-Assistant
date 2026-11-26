from invest_blog.profit_protection_study import compare_exits

def test_protection_only_arms_after_trigger_minute_not_intraminute_hindsight():
    bars=[{'t':1,'o':100,'h':111,'l':95,'c':105},{'t':2,'o':105,'h':106,'l':99,'c':100},{'t':3,'o':100,'h':101,'l':89,'c':90}]
    r=compare_exits(bars,100,90,120,'long',1,2)
    assert r['baseline']['exit']==89 and r['baseline']['at']==3
    assert r['break_even']['exit']==102 and r['break_even']['at']==2

def test_protection_can_remove_a_later_winner_and_stop_wins_ambiguous_minute():
    bars=[{'t':1,'o':100,'h':112,'l':95,'c':105},{'t':2,'o':105,'h':106,'l':99,'c':100},{'t':3,'o':100,'h':121,'l':99,'c':120}]
    r=compare_exits(bars,100,90,120,'long',0)
    assert r['baseline']['exit']==120 and r['break_even']['exit']==100
    r=compare_exits([{'t':1,'o':100,'h':121,'l':89,'c':100}],100,90,120,'long',0)
    assert r['baseline']['exit']==r['break_even']['exit']==90
