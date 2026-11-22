import pytest
from invest_blog.walt_trading import group_for,check_group_limit

@pytest.mark.parametrize('symbol,expected',[('USDJPY','forex'),('EURUSD','forex'),('XCU','commodities'),
    ('NVDA','technology'),('BTC','crypto'),('ETH','crypto'),('SOL','crypto'),('UNKNOWN','other'),('USDT','other')])
def test_known_classes_and_conservative_unknown_fallback(symbol,expected):
    assert group_for(symbol)==expected

def test_fx_and_copper_do_not_fill_crypto_bucket():
    catalog={0:{'base_asset':'USDJPY'},1:{'base_asset':'XCU'},2:{'base_asset':'NVDA'}}
    positions=[{'contract_id':i,'side':'short'} for i in catalog]
    check_group_limit('BTC',catalog,positions,2)
    check_group_limit('ETH',catalog,positions,2)
    check_group_limit('SOL',catalog,positions,2)

def test_two_crypto_positions_still_block_third_regardless_of_side():
    catalog={0:{'base_asset':'BTC'},1:{'base_asset':'ETH'}}
    with pytest.raises(ValueError,match='Криптоактивы.*2 из 2.*BTC, ETH'):
        check_group_limit('SOL',catalog,[{'contract_id':0,'side':'long'},{'contract_id':1,'side':'short'}],2)

def test_unknown_assets_remain_in_shared_capped_bucket():
    catalog={0:{'base_asset':'UNKNOWN_A'},1:{'base_asset':'UNKNOWN_B'}}
    with pytest.raises(ValueError,match='Неклассифицированные'):
        check_group_limit('UNKNOWN_C',catalog,[{'contract_id':0},{'contract_id':1}],2)
