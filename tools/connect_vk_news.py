"""One-time official VK ID login. --cloud explicitly uploads encrypted GitHub secret."""
import argparse,json,secrets,sys,time,webbrowser,subprocess,os,base64
from pathlib import Path
from http.server import HTTPServer,BaseHTTPRequestHandler
from urllib.parse import urlsplit,parse_qs
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from invest_blog.vk_news_auth import authorization_url,exchange,REDIRECT
from invest_blog.cloud_news_vk import VKNewsSender

def upload(payload):
    import requests
    from nacl.public import PublicKey,SealedBox
    root=Path(__file__).resolve().parents[1]
    remote=subprocess.run(['git','remote','get-url','origin'],cwd=root,capture_output=True,text=True,check=True).stdout.strip()
    if remote not in ('https://github.com/KirillDmitruk/Invest-Blog-Assistant','https://github.com/KirillDmitruk/Invest-Blog-Assistant.git','git@github.com:KirillDmitruk/Invest-Blog-Assistant.git'):raise ValueError('Unexpected repository')
    reply=subprocess.run(['git','credential','fill'],input='protocol=https\nhost=github.com\n\n',capture_output=True,text=True,env=os.environ|{'GIT_TERMINAL_PROMPT':'0','GCM_INTERACTIVE':'Never'},check=True)
    fields=dict(line.split('=',1) for line in reply.stdout.splitlines() if '=' in line)
    if not fields.get('password'):raise ValueError('GitHub login required')
    s=requests.Session();s.headers.update(Authorization='Bearer '+fields['password'],Accept='application/vnd.github+json')
    base='https://api.github.com/repos/KirillDmitruk/Invest-Blog-Assistant'
    response=s.get(base+'/actions/secrets/public-key',timeout=25);response.raise_for_status();key=response.json()
    sealed=SealedBox(PublicKey(base64.b64decode(key['key']))).encrypt(json.dumps(payload).encode())
    response=s.put(base+'/actions/secrets/NEWS_VK_AUTH',json={'key_id':key['key_id'],'encrypted_value':base64.b64encode(sealed).decode()},timeout=25);response.raise_for_status()
    for name,value in [('NEWS_VK_GROUP_ID',str(payload['group_id'])),('NEWS_VK_ENABLED','true')]:
        response=s.patch(base+'/actions/variables/'+name,json={'name':name,'value':value},timeout=25)
        if response.status_code==404:response=s.post(base+'/actions/variables',json={'name':name,'value':value},timeout=25)
        response.raise_for_status()

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--app-id',required=True);parser.add_argument('--group-id',type=int,default=243744746);parser.add_argument('--cloud',action='store_true');args=parser.parse_args()
    if args.group_id!=243744746:raise ValueError('Destination must be the approved community')
    nonce=secrets.token_urlsafe(32);verifier=secrets.token_urlsafe(64);received={}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            p=urlsplit(self.path);q=parse_qs(p.query)
            if p.path!='/callback' or q.get('state')!=[nonce]:self.send_error(400);return
            if q.get('code') and q.get('device_id'):received.update(code=q['code'][0],device_id=q['device_id'][0])
            else:received['error']=True
            self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Cache-Control','no-store');self.send_header('Referrer-Policy','no-referrer');self.end_headers();self.wfile.write('<meta charset="utf-8"><p>Вернись в терминал Content Hub. Адрес этой страницы никому не пересылай.</p>'.encode())
    with HTTPServer(('127.0.0.1',8771),Handler) as server:
        server.timeout=1;url=authorization_url(args.app_id,nonce,verifier)
        print('Открываю официальный вход VK ID. Подтверди публикацию на стене своего сообщества. Коды и адрес возврата не пересылай.')
        webbrowser.open(url);deadline=time.monotonic()+600
        while not received and time.monotonic()<deadline:server.handle_request()
    if not received or received.get('error'):raise ValueError('VK login not completed')
    auth=exchange(args.app_id,received['code'],received['device_id'],nonce,verifier);VKNewsSender(args.group_id,auth['access_token']);auth['group_id']=args.group_id
    from invest_blog.walt_agent import protect
    path=Path(__file__).resolve().parents[1]/'data/vk-news-auth.dpapi';path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(protect(json.dumps(auth).encode()))
    if args.cloud:upload(auth);print('Готово: вход сохранён в GitHub Secret NEWS_VK_AUTH, дублирование включено. Ключи не выведены.')
    else:print('Вход сохранён локально. Для облачной отправки повтори команду с --cloud.')
if __name__=='__main__':
    try:main()
    except Exception as exc:raise SystemExit('Подключение VK не завершено: '+(str(exc) if isinstance(exc,ValueError) else type(exc).__name__)+'. Ключи скрыты.') from None
