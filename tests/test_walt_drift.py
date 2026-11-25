from decimal import Decimal
import pytest
from invest_blog.walt_trading import calculate,reserve_entry_drift,DEFAULT_LIMITS

def contract():
    return {'contract_id':4099,'base_asset':'DRV','quote_asset':'USDC','max_leverage':3,
        'price_slippage':'.01','taker_fee_pct':'.0007','funding_rate_hourly':'.000016','tick_size':'.00001','step_size':'.1',
        'min_size_quote':'1','max_size_quote':'25000','min_size_base':'.1','max_size_base':'1000000'}

@pytest.mark.parametrize('side',['long','short'])
def test_reserve_keeps_exact_amount_valid_at_allowed_adverse_price_drift(side):
    c=contract();limits={**DEFAULT_LIMITS,'leverage':2};price=Decimal('.4')
    p=calculate(c,price,'50','50',limits,side)
    buffered=reserve_entry_drift(c,p,'50','50',limits,Decimal(0))
    assert Decimal(buffered['margin'])<Decimal(p['margin'])
    assert buffered['reference_price']==p['reference_price'] and buffered['stop']==p['stop'] and buffered['take']==p['take']
    for move in ('0','.001','.0049','.005'):
        fresh=price*(1+(1 if side=='long' else -1)*Decimal(move))
        current=calculate(c,fresh,'50','50',limits,side,requested=buffered['margin'],fixed=buffered)
        assert current['margin']==buffered['margin'] and Decimal(current['risk_estimate'])<=Decimal('.50')

def test_full_budget_quote_reproduces_confirmation_failure_but_reserve_fixes_it():
    c=contract();limits={**DEFAULT_LIMITS,'leverage':2};p=calculate(c,'.4','50','50',limits,'long')
    with pytest.raises(ValueError,match='сейчас максимум'):
        calculate(c,'.4008','50','50',limits,'long',requested=p['margin'],fixed=p)
    buffered=reserve_entry_drift(c,p,'50','50',limits,Decimal(0))
    calculate(c,'.4008','50','50',limits,'long',requested=buffered['margin'],fixed=buffered)

def test_later_balance_loss_still_blocks_the_original_amount():
    c=contract();limits={**DEFAULT_LIMITS,'leverage':2};p=calculate(c,'.4','50','50',limits,'long')
    buffered=reserve_entry_drift(c,p,'50','50',limits,Decimal(0))
    with pytest.raises(ValueError,match='сейчас максимум'):
        calculate(c,'.4','50','30',limits,'long',requested=buffered['margin'],fixed=buffered)
