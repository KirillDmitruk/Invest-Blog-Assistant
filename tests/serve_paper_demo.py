"""Static UI fixture only: no app database, credentials or remote market calls."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import sys,json,time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from invest_blog.paper_perps import initial
ROOT=Path(__file__).resolve().parents[1]/'invest_blog/web_static'
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        path=self.path.split('?')[0]
        if path=='/api/paper/overview':
            state=initial();state.update(last_cycle=time.time(),reason='ДЕМОНСТРАЦИОННЫЕ ДАННЫЕ. Ожидаю подтверждённый сигнал.')
            raw=json.dumps({'configured':True,'mode':'paper','sync':'ok','state':state}).encode();mime='application/json'
        elif path.startswith('/api/'):
            self.send_response(503);self.end_headers();return
        else:
            file=ROOT/'index.html' if path=='/' else (ROOT/path.removeprefix('/static/')).resolve()
            if not file.is_relative_to(ROOT) or not file.is_file():self.send_response(404);self.end_headers();return
            raw=file.read_bytes();mime='text/javascript' if file.suffix=='.js' else 'text/css' if file.suffix=='.css' else 'text/html; charset=utf-8'
        self.send_response(200);self.send_header('Content-Type',mime);self.end_headers();self.wfile.write(raw)
if __name__=='__main__':ThreadingHTTPServer(('127.0.0.1',8778),Handler).serve_forever()
