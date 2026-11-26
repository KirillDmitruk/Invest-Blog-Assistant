"""Durable VK mirror of exactly the text already published to Telegram."""
from datetime import datetime,timedelta,timezone
import hashlib,os,re
from .vk_publisher import VKPublisher,VKConfig

def configured_target():
    if os.getenv('NEWS_VK_ENABLED')!='true':return None
    raw=os.getenv('NEWS_VK_GROUP_ID','').strip()
    if not re.fullmatch(r'[1-9][0-9]{0,15}',raw):raise ValueError('VK destination not configured')
    return int(raw)

def enqueue(state,key,text,source_url,group_id,created_at):
    out=state.setdefault('vk_outbox',{})
    ident=hashlib.sha256((str(group_id)+':'+key).encode()).hexdigest()[:32]
    if ident in state.get('vk_deliveries',{}) or ident in out:return
    out[ident]={'telegram_key':key,'group_id':group_id,'text':text,'source_url':source_url,'created_at':created_at}
    size=0;keep={}
    for ident,row in sorted(out.items(),key=lambda p:p[1]['created_at'],reverse=True):
        length=len(row['text'].encode('utf-8'))+len(row['source_url'].encode('utf-8'))+500
        if len(keep)>=50 or size+length>150_000:
            state['vk_dropped']=state.get('vk_dropped',0)+1;continue
        keep[ident]=row;size+=length
    state['vk_outbox']=keep

class VKNewsSender:
    def __init__(self,group_id,token,session=None):
        self.publisher=VKPublisher(VKConfig(group_id,token),session)
        reply=self.publisher._call('groups.getById',group_ids=str(group_id),fields='is_admin,admin_level,can_post')
        rows=reply.get('groups',[])
        if len(rows)!=1 or rows[0].get('id')!=group_id or rows[0].get('is_admin')!=1 or rows[0].get('admin_level',0)<2 or rows[0].get('can_post')!=1:
            raise ValueError('VK target or publishing rights not confirmed')
    def send(self,row,ident):
        return self.publisher.publish(row['text'],guid='news-'+ident,attachment_url=row['source_url'])

def drain(store,group_id,token,*,now=None,factory=VKNewsSender,auth_bundle=None):
    if group_id is None:return {'status':'disabled'}
    if not token and not auth_bundle:return {'status':'missing_credentials','group_id':group_id}
    clock=now or datetime.now(timezone.utc)
    if not 9<=clock.astimezone(timezone(timedelta(hours=3))).hour<23:return {'status':'outside_hours','group_id':group_id}
    if auth_bundle:
        from .vk_news_auth import cloud_token
        try:token=cloud_token(store,auth_bundle,group_id)
        except Exception:return {'status':'authorization_failed','group_id':group_id}
    state=store.load();receipts=state.setdefault('vk_deliveries',{});out=state.setdefault('vk_outbox',{})
    # Expired items and attempts are never sent again, including ambiguous results.
    keep={k:r for k,r in out.items() if k not in receipts and timedelta(0)<=clock-datetime.fromisoformat(r['created_at'])<=timedelta(hours=24)}
    if keep!=out:state['vk_outbox']=out=keep;store.save(state)
    recent=any(r.get('group_id')==group_id and timedelta(0)<=clock-datetime.fromisoformat(r['created_at'])<timedelta(minutes=10) for r in receipts.values())
    if recent:return {'status':'rate_limited','group_id':group_id}
    candidates=[(k,r) for k,r in out.items() if r['group_id']==group_id and state['deliveries'].get(r['telegram_key'],{}).get('status')=='sent']
    if not candidates:return {'status':'idle','group_id':group_id}
    ident,row=min(candidates,key=lambda pair:pair[1]['created_at'])
    try:sender=factory(group_id,token)
    except Exception:return {'status':'authorization_failed','group_id':group_id}
    receipt={'status':'attempted','created_at':clock.isoformat(),'group_id':group_id,'telegram_key':row['telegram_key']}
    receipts[ident]=receipt
    state['vk_deliveries']=dict(sorted(receipts.items(),key=lambda p:p[1]['created_at'])[-100:])
    store.save(state) # Reserve before wall.post, independently of Telegram delivery.
    try:
        receipt['post_url']=sender.send(row,ident);receipt['status']='sent'
    except Exception:receipt['status']='uncertain'
    state['vk_outbox'].pop(ident,None);store.save(state)
    return {'status':receipt['status'],'group_id':group_id,**({'post_url':receipt['post_url']} if 'post_url' in receipt else {})}
