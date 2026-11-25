"""Delayed fill simulation. All wallet operations are FakeAgent calls."""
from pathlib import Path
from tempfile import TemporaryDirectory
import sys,json
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from test_walt_preview import native_service
from test_walt_trading import wrapped
from invest_blog.dashboard import create_app
from invest_blog.editorial_workflow import EditorialWorkflow
import invest_blog.walt_trade_routes as routes
import invest_blog.walt_agent_routes as agent_routes
import uvicorn

if __name__=='__main__':
    EditorialWorkflow._find_online_titles=lambda self:('','')
    with TemporaryDirectory(prefix='walt-settlement-demo-') as folder:
        service=native_service(Path(folder));service.signal();original=service.agent.rpc
        pending={'positions':None,'reads':0}
        def rpc(method,params=None,*args,**kw):
            name=params['name']
            if name=='perps_list_positions' and pending['positions'] is not None:
                pending['reads']+=1
                if pending['reads']>=2:service.agent.positions=pending['positions']
            value,sid=original(method,params,*args,**kw)
            if name=='perps_execute_operation':
                pending['positions']=service.agent.positions;service.agent.positions=[]
                return wrapped({'order':{'order_id':'test-order','status':'pending'}}),sid
            if name=='perps_get_order' and not service.agent.positions:
                return wrapped({'order':{'order_id':'test-order','status':'processing'}}),sid
            return value,sid
        service.agent.rpc=rpc
        routes.WaltAgent=lambda _:service.agent
        routes.WaltTrading=lambda _:service
        agent_routes.WaltAgent=lambda _:service.agent
        uvicorn.run(create_app(service.agent.path),host='127.0.0.1',port=8767,log_level='warning')
