from html import escape
import pytest
from invest_blog.cloud_news import ChannelSender,target_channel
from invest_blog.cloud_signals import ServiceCallError
from invest_blog.news_article import source_image,Article

PHOTO='https://smart-lab.ru/uploads/2026/images/news.png'
def sender():
    s=ChannelSender.__new__(ChannelSender);s.escape=escape;s.session=object();s.url='https://example.test/bot/';s.chat=-1001;s.channel='@investdemo';return s

def test_missing_target_has_no_previous_channel_fallback(monkeypatch):
    monkeypatch.delenv('NEWS_TELEGRAM_CHANNEL',raising=False)
    with pytest.raises(ValueError,match='не настроен'):target_channel()

def test_only_article_images_are_selected():
    p=Article();p.feed('<img src="/uploads/logo.png"><div class="topic"><div class="content"><a class="imgpreview" href="/uploads/chart.png"><img src="/uploads/thumb.webp"></a><div class="lock-card"><img src="/uploads/ad.png"></div></div></div>')
    assert p.images==['https://smart-lab.ru/uploads/chart.png']
    assert source_image('https://evil.test/uploads/a.png') is None
    assert source_image('https://smart-lab.ru@evil.test/uploads/a.png') is None

def test_photo_sent_as_one_post_with_bold_first_line(monkeypatch):
    calls=[]
    def call(*args,**kw):calls.append((args[2],kw['json']));return {'ok':True,'result':{'message_id':9}}
    monkeypatch.setattr('invest_blog.cloud_news.json_call',call)
    s=sender();assert s.send('Заголовок новости\n\nТекст.',photo=PHOTO,source_url='https://smart-lab.ru/blog/123.php')=='https://t.me/investdemo/9'
    assert len(calls)==1 and calls[0][0].endswith('sendPhoto')
    assert calls[0][1]['caption'].startswith('<b>Заголовок новости</b>')
    assert s.media_mode=='photo'

def test_long_post_not_cut_to_photo_caption(monkeypatch):
    calls=[]
    def call(*args,**kw):calls.append((args[2],kw['json']));return {'ok':True,'result':{'message_id':9}}
    monkeypatch.setattr('invest_blog.cloud_news.json_call',call)
    text='Заголовок\n\n'+'Полный текст. '*120
    sender().send(text,photo=PHOTO,source_url='https://smart-lab.ru/blog/123.php')
    assert calls[0][0].endswith('sendMessage')
    assert 'Полный текст. '*120 in calls[0][1]['text']
    assert calls[0][1]['link_preview_options']['show_above_text'] is True

def test_ambiguous_photo_send_never_falls_back(monkeypatch):
    calls=[]
    def call(*args,**kw):calls.append(args[2]);raise ServiceCallError(kind='Timeout')
    monkeypatch.setattr('invest_blog.cloud_news.json_call',call)
    with pytest.raises(ServiceCallError):sender().send('Заголовок\n\nТекст.',photo=PHOTO)
    assert len(calls)==1

def test_explicit_photo_rejection_can_fall_back(monkeypatch):
    calls=[]
    def call(*args,**kw):
        calls.append(args[2])
        if len(calls)==1:raise ServiceCallError(400)
        return {'ok':True,'result':{'message_id':9}}
    monkeypatch.setattr('invest_blog.cloud_news.json_call',call)
    sender().send('Заголовок\n\nТекст.',photo=PHOTO)
    assert [x.rsplit('/',1)[-1] for x in calls]==['sendPhoto','sendMessage']
