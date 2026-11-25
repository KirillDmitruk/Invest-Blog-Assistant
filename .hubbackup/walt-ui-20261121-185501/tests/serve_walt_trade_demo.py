"""Temporary demo: every wallet request and execution uses FakeAgent."""
from pathlib import Path
from tempfile import TemporaryDirectory
import sys,json
from datetime import datetime,timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from test_walt_trading import FakeAgent,DemoHistory
from invest_blog.walt_trading import WaltTrading
from invest_blog.walt_agent import WaltAgent
from invest_blog.walt_news import NoTradingNews
from invest_blog.dashboard import create_app
from invest_blog.editorial_workflow import EditorialWorkflow
import invest_blog.walt_trade_routes as routes
import invest_blog.walt_agent_routes as agent_routes
import uvicorn

class DemoNews(NoTradingNews):
    def collect(self,progress=None):
        return {'status':'ok','sources':[],'checked_at':datetime.now(timezone.utc).isoformat(),'items':[]}
    def context(self,snapshot,symbol):
        return {'status':'matched','source_status':'ok','checked_at':snapshot['checked_at'],'note':'Демонстрационные новости, не реальное событие.',
            'items':[{'id':'demo-news','source':'Демо RSS','title':'Bitcoin: демонстрационная новость для проверки интерфейса',
            'summary':'Имитация новостного контекста тестового кошелька.','published_at':snapshot['checked_at'],
            'url':'https://example.com/demo-btc','scope':'asset','cache_stale':False}]}

if __name__=='__main__':
    EditorialWorkflow._find_online_titles=lambda self:('','')
    with TemporaryDirectory(prefix='walt-trade-demo-') as folder:
        path=Path(folder)/'demo.db'
        agent=FakeAgent(path)
        agent.local_request=lambda *args,**kwargs:{'message':{'content':json.dumps({'signals':[{'contract_id':0,'side':'wait','reason':'Нет истории для подтверждения тренда.'}]})}}
        routes.WaltAgent=lambda _:agent
        routes.WaltTrading=lambda a:WaltTrading(a,history_factory=DemoHistory,news_factory=DemoNews)
        agent_routes.WaltAgent=lambda _:agent
        uvicorn.run(create_app(path),host='127.0.0.1',port=8768,log_level='warning')
