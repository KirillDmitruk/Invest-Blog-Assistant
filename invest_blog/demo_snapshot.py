"""Read encrypted public repository files through the current Contents API.

Branch URLs on raw.githubusercontent.com can lag behind committed state.
No GitHub token or broker credential is sent by this reader.
"""
import base64,json,re,time

BASE='https://api.github.com/repos/KirillDmitruk/Invest-Blog-Assistant/'
ALLOWED={('automation/paper-perps-state','paper-state.enc'),
         ('automation/tinvest-paper-state','tinvest-paper.enc')}

def encrypted_snapshot(session,branch,filename,force=False):
    if (branch,filename) not in ALLOWED:raise ValueError('Unknown demo snapshot')
    def get(path,params):
        with session.get(BASE+path,params=params,headers={'Cache-Control':'no-cache','Accept':'application/vnd.github+json'},
                         timeout=(5,20),allow_redirects=False,stream=True) as r:
            if r.status_code!=200:raise ValueError('Cloud snapshot unavailable')
            chunks=[];size=0
            for chunk in r.iter_content(65536):
                size+=len(chunk)
                if size>4_000_000:raise ValueError('Snapshot too large')
                chunks.append(chunk)
        return json.loads(b''.join(chunks))
    meta=get('contents/'+filename,{'ref':branch,'v':time.time_ns() if force else int(time.time()//300)})
    if not meta.get('content'):
        sha=meta.get('sha','')
        if not re.fullmatch('[a-f0-9]{40}',sha):raise ValueError('Invalid snapshot version')
        meta=get('git/blobs/'+sha,{})
    if meta.get('encoding')!='base64':raise ValueError('Unexpected snapshot encoding')
    raw=base64.b64decode(meta['content'])
    if len(raw)>2_000_000:raise ValueError('Encrypted snapshot too large')
    return raw
