"""Temporary demo: every wallet request and execution uses FakeAgent."""
from pathlib import Path
from tempfile import TemporaryDirectory
import sys,json
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from test_walt_trading import FakeAgent,DemoHistory
from invest_blog.walt_trading import WaltTrading
from invest_blog.walt_agent import WaltAgent
from invest_blog.dashboard import create_app
from invest_blog.editorial_workflow import EditorialWorkflow
import invest_blog.walt_trade_routes as routes
import invest_blog.walt_agent_routes as agent_routes
import uvicorn

if __name__=='__main__':
    EditorialWorkflow._find_online_titles=lambda self:('','')
    with TemporaryDirectory(prefix='walt-trade-demo-') as folder:
        path=Path(folder)/'demo.db'
        agent=FakeAgent(path)
        agent.local_request=lambda *args,**kwargs:{'message':{'content':json.dumps({'signals':[{'contract_id':0,'side':'wait','reason':'Нет истории для подтверждения тренда.'}]})}}
        routes.WaltAgent=lambda _:agent
        routes.WaltTrading=lambda a:WaltTrading(a,history_factory=DemoHistory)
        agent_routes.WaltAgent=lambda _:agent
        uvicorn.run(create_app(path),host='127.0.0.1',port=8768,log_level='warning')
