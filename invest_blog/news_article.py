"""Bounded public article extraction from an explicit publisher/path allowlist."""
from html.parser import HTMLParser
import re
from urllib.parse import urlsplit, urljoin

def source_image(value):
    if not isinstance(value,str) or not value:return None
    value=urljoin('https://smart-lab.ru/',value)
    p=urlsplit(value)
    try:valid_port=p.port in (None,443)
    except ValueError:return None
    if p.scheme!='https' or p.hostname!='smart-lab.ru' or p.username or p.password or not valid_port or p.query or p.fragment:return None
    if not re.fullmatch(r'/uploads/[A-Za-z0-9_./-]+\.(?:jpe?g|png)',p.path,re.I):return None
    return value

class Article(HTMLParser):
    def __init__(self):
        super().__init__();self.depth=0;self.topic=False;self.root=None;self.skip=None;self.script=False;self.done=False;self.parts=[];self.images=[]
    def handle_starttag(self,tag,attrs):
        a=dict(attrs);classes=a.get('class','').split()
        if tag=='div':
            self.depth+=1
            if 'topic' in classes:self.topic=True
            if self.topic and self.root is None and not self.done and 'content' in classes:self.root=self.depth
            if self.root and self.skip is None and any(c.startswith('lock-card') for c in classes):self.skip=self.depth
        if self.root and not self.skip and not self.script:
            candidate=a.get('href') if tag=='a' and 'imgpreview' in classes else a.get('src') if tag=='img' else None
            photo=source_image(candidate)
            if photo:self.images.append(photo)
        if tag in ('script','style'):self.script=True
        if tag in ('p','br','li'):self.parts.append(' ')
    def handle_endtag(self,tag):
        if tag=='div':
            if self.root==self.depth:self.root=None;self.done=True
            if self.skip==self.depth:self.skip=None
            self.depth-=1
        if tag in ('script','style'):self.script=False
    def handle_data(self,data):
        if self.root and not self.skip and not self.script:self.parts.append(data)
    def text(self):return re.sub(r'\s+',' ',' '.join(self.parts)).strip()


def enrich(item,session):
    parts=urlsplit(item['url'])
    if parts.scheme!='https' or parts.hostname!='smart-lab.ru' or parts.username or parts.password or parts.port not in (None,443) or parts.query or not re.fullmatch(r'/blog/(?:news/)?\d+\.php',parts.path):
        return item
    # The news RSS uses /blog/id.php, which redirects to /blog/news/id.php.
    # Construct the known canonical path; do not follow arbitrary Location headers.
    request_url='https://smart-lab.ru/blog/news/'+parts.path.rsplit('/',1)[-1]
    with session.get(request_url,timeout=(5,15),allow_redirects=False,stream=True) as response:
        if response.status_code!=200:raise ValueError('Статья недоступна')
        chunks=[];size=0
        for chunk in response.iter_content(65536):
            size+=len(chunk)
            if size>2_000_000:raise ValueError('Слишком большая статья')
            chunks.append(chunk)
    parser=Article();parser.feed(b''.join(chunks).decode('utf-8'))
    text=parser.text()
    if len(text)<80:raise ValueError('В статье недостаточно текста')
    return {**item,'summary':text[:3000], 'image_url':parser.images[0] if parser.images else None}
