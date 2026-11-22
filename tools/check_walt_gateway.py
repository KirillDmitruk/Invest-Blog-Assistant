"""End-to-end gateway test: READ callback only. No prepare/execute actions."""
import json,os,secrets,sys,time
from pathlib import Path
from tempfile import TemporaryDirectory
import requests
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from invest_blog.walt_cloud_state import CloudWaltState,CloudWaltAgent
from invest_blog.walt_trading import WaltTrading
from invest_blog.telegram_walt import TradeBot
from invest_blog.walt_telegram_protocol import button

def main():
    session=requests.Session();owner=os.environ['SIGNALS_CHAT_ID'];bot=os.environ['SIGNALS_BOT_TOKEN']
    url=os.environ['WALT_GATEWAY_URL']
    if not url.startswith('https://content-hub-walt-callbacks.') or not url.endswith('.workers.dev'):raise ValueError('Gateway URL invalid')
    if session.get(url,timeout=30).text!='Walt callback gateway':raise ValueError('Gateway health not confirmed')
    if session.post(url,json={},timeout=30).status_code!=403:raise ValueError('Unsigned access not rejected')
    def tg(method,data):
        value=session.post('https://api.telegram.org/bot'+bot+'/'+method,json=data,timeout=30).json()
        if not value.get('ok'):raise ValueError('Telegram check not confirmed')
        return value['result']
    me=tg('getMe',{});info=tg('getWebhookInfo',{})
    if info.get('url')!=url:raise ValueError('Webhook URL mismatch')
    with TemporaryDirectory(prefix='walt-check-') as folder:
        state=CloudWaltState(Path(folder)/'state.db',os.environ['GITHUB_REPOSITORY'],os.environ['GH_STATE_TOKEN'],os.environ['WALT_CLOUD_STATE_KEY'].encode())
        state.restore();agent=CloudWaltAgent(state);service=WaltTrading(agent)
        # Explicitly read existing wallet state; never create/preview any order.
        sid=agent.connect();service.read('balance_get',sid=sid);service.read('perps_list_positions',sid=sid)
        with agent.db() as db:row=db.execute("SELECT id FROM walt_trade_drafts WHERE status IN ('protected','not_open','resolved_absent') ORDER BY at DESC LIMIT 1").fetchone()
        if not row:raise ValueError('No existing journal entry for read-only test')
        draft=service.public(row['id'])
        controller=TradeBot(service,owner,os.environ['WALT_BUTTON_KEY'],lambda *args:None,state.checkpoint)
        expiry=int(time.time()+1800);nonce=controller.register_button(draft,'read',expiry)
        data=button(f'r:{nonce}:{expiry}',owner,os.environ['WALT_BUTTON_KEY'])
        state.checkpoint()
        message=tg('sendMessage',{'chat_id':owner,'text':'☁️ Облачное подключение Walt проверено.\nПроверяю чтение состояния через кнопки. Новые сделки не открываются.',
            'reply_markup':{'inline_keyboard':[[{'text':'🔄 Проверить состояние Walt','callback_data':data}]]}})
        with agent.db() as db:db.execute('UPDATE walt_tg_buttons SET message_id=? WHERE nonce=?',(str(message['message_id']),nonce))
        state.checkpoint()
    # A synthetic READ-only callback exercises authentication + encrypted dispatch.
    # It cannot confirm/open an order: action r and kind read are fixed above.
    update={'callback_query':{'id':'cloudcheck_'+secrets.token_hex(8),'data':data,'from':{'id':int(owner)},
        'message':{'message_id':message['message_id'],'from':{'id':me['id']},'chat':{'id':int(owner),'type':'private'}}}}
    result=session.post(url,json=update,headers={'X-Telegram-Bot-Api-Secret-Token':os.environ['TG_WEBHOOK_SECRET']},timeout=30)
    if result.status_code!=200 or result.text!='OK':raise ValueError('Read-only callback dispatch not confirmed')
    print('Gateway health, unsigned rejection, webhook, wallet reads and READ-only callback dispatch verified. No orders sent.')

if __name__=='__main__':
    try:main()
    except Exception:raise SystemExit('Read-only gateway check not confirmed. Details withheld; no orders sent.') from None
