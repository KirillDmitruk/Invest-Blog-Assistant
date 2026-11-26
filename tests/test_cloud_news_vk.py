from copy import deepcopy
from datetime import timedelta
import pytest
from test_cloud_news import Store,Sender,item,editor,NOW
from invest_blog.cloud_news import publish
from invest_blog.cloud_news_vk import drain,enqueue,VKNewsSender

def queued():
    s=Store();tg=Sender(s)
    assert publish(s,tg,[item()],editor=editor,now=NOW,send=True,mirror_group=243744746)['status']=='sent'
    return s,tg

def test_same_text_and_independent_durable_vk_reservation():
    s,tg=queued();seen=[]
    class VK:
        def __init__(self,group,token):assert group==243744746
        def send(self,row,ident):
            assert s.state['vk_deliveries'][ident]['status']=='attempted'
            assert row['text']==tg.sent[0];seen.append(row['text'])
            return 'https://vk.com/wall-243744746_1'
    assert drain(s,243744746,'fake',now=NOW,factory=VK)['status']=='sent'
    assert drain(s,243744746,'fake',now=NOW+timedelta(minutes=11),factory=VK)['status']=='idle'
    assert len(seen)==len(tg.sent)==1

def test_vk_timeout_never_reposts_to_vk_or_telegram():
    s,tg=queued();calls=[]
    class VK:
        def __init__(self,*args):pass
        def send(self,row,ident):calls.append(ident);raise TimeoutError('secret must not leak')
    assert drain(s,243744746,'fake',now=NOW,factory=VK)['status']=='uncertain'
    assert drain(s,243744746,'fake',now=NOW+timedelta(minutes=11),factory=VK)['status']=='idle'
    assert len(calls)==len(tg.sent)==1

def test_missing_token_and_failed_preflight_never_attempt_wall_post():
    s,tg=queued()
    assert drain(s,243744746,'',now=NOW)['status']=='missing_credentials'
    def fail(*args):raise ValueError('private context')
    assert drain(s,243744746,'fake',now=NOW,factory=fail)['status']=='authorization_failed'
    assert not s.state.get('vk_deliveries') and len(tg.sent)==1

def test_uncertain_telegram_post_is_not_mirrored_and_preview_creates_no_outbox():
    s=Store();tg=Sender(s,fail=True)
    assert publish(s,tg,[item()],editor=editor,now=NOW,send=True,mirror_group=243744746)['status']=='uncertain'
    assert drain(s,243744746,'fake',now=NOW)['status']=='idle'
    s=Store();publish(s,None,[item()],editor=editor,now=NOW,mirror_group=243744746)
    assert s.saves==0 and 'vk_outbox' not in s.state

def test_vk_reservation_failure_prevents_request():
    s,tg=queued();old=s.save
    def save(state):
        if state.get('vk_deliveries'):raise ValueError('Storage unavailable')
        old(state)
    s.save=save
    class VK:
        def __init__(self,*args):pass
        def send(self,*args):pytest.fail('No reservation means no post')
    with pytest.raises(ValueError):drain(s,243744746,'fake',now=NOW,factory=VK)

def test_group_validation_requires_exact_target_admin_and_post_rights():
    from test_vk_publisher import Session
    row={'id':243744746,'is_admin':1,'admin_level':3,'can_post':1}
    VKNewsSender(243744746,'fake',Session({'response':{'groups':[row]}}))
    for key,value in [('id',1),('is_admin',0),('can_post',0),('admin_level',1)]:
        with pytest.raises(ValueError):VKNewsSender(243744746,'fake',Session({'response':{'groups':[{**row,key:value}]}}))
