"""Low maximum-leverage contracts: retain conservative protection and risk caps."""
from decimal import Decimal
import pytest
from invest_blog.walt_trading import calculate,DEFAULT_LIMITS
from invest_blog.walt_preview import protection_evidence

@pytest.mark.parametrize('side',['long','short'])
def test_lower_leverage_passes_same_protection_with_unchanged_risk_cap(side):
    contract={'contract_id':4099,'base_asset':'DRV','quote_asset':'USDC','max_leverage':3,
        'margin':{'min_initial_margin_fraction':3333,'maintenance_margin_fraction':2000,'closeout_margin_fraction':1333},
        'price_slippage':'.01','taker_fee_pct':'.0007','funding_rate_hourly':'.000016','tick_size':'.00001','step_size':'.1',
        'min_size_quote':'1','max_size_quote':'25000','min_size_base':'.1','max_size_base':'1000000'}
    limits={**DEFAULT_LIMITS,'leverage':3};before=dict(limits)
    upper=calculate(contract,'.4','100','100',limits,side)
    lower=calculate(contract,'.4','100','100',{**limits,'leverage':2},side)
    assert not protection_evidence(upper,contract,{})['safe']
    assert protection_evidence(lower,contract,{})['safe']
    assert lower['leverage']==2 and Decimal(lower['risk_estimate'])<=Decimal('1')
    assert Decimal(lower['margin'])<=Decimal(limits['max_margin']) and limits==before
