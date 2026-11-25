import os
from pathlib import Path
import pytest
from invest_blog.walt_agent import WaltAgent
from invest_blog.paper_perps import initial

@pytest.mark.parametrize('name',['perps_execute_operation','perps_prepare_order_place','perps_readiness'])
def test_paper_mode_blocks_real_tools_before_any_access_token_read(monkeypatch,name):
    monkeypatch.setenv('TRADING_MODE','paper');agent=object.__new__(WaltAgent)
    with pytest.raises(ValueError,match='PAPER'):agent.rpc('tools/call',{'name':name},_manual=True)

def test_paper_workflow_has_no_broker_credentials():
    source=Path('.github/workflows/paper-perps.yml').read_text()
    assert 'WALT_CLOUD_STATE_KEY' not in source and 'TINVEST_TOKEN' not in source
    assert 'PAPER_STATE_KEY' in source and 'TRADING_MODE' in source

def test_failed_state_save_never_sends_a_notification(monkeypatch):
    import invest_blog.paper_cloud as cloud
    from test_paper_perps import Market,report,NOW
    monkeypatch.setenv('TRADING_MODE','paper');monkeypatch.setenv('PAPER_ENABLED','true')
    for name in ('GITHUB_REPOSITORY','GH_STATE_TOKEN','PAPER_STATE_KEY'):monkeypatch.setenv(name,'fake')
    class Store:
        def __init__(self,*a):pass
        def load(self):return initial()
        def save(self,state):raise ValueError('storage unavailable')
    monkeypatch.setattr(cloud,'PaperStore',Store);monkeypatch.setattr(cloud,'PublicPaperMarket',Market)
    monkeypatch.setattr(cloud,'latest_report',lambda now:report());monkeypatch.setattr(cloud.time,'time',lambda:NOW)
    def forbidden(*args,**kwargs):raise AssertionError('Notification sent before durable state')
    monkeypatch.setattr(cloud,'json_call',forbidden)
    with pytest.raises(SystemExit,match='Paper cycle not confirmed'):cloud.main()
