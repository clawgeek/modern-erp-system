# -*- coding: utf-8 -*-
"""云枢 ERP · 零依赖服务入口。

    python server.py                 # 默认 http://127.0.0.1:8766
    python server.py --port 8080     # 指定端口
    python server.py --reset         # 重置数据库并重新灌入种子数据

只依赖 Python 标准库：http.server + sqlite3。生产部署见 Dockerfile / docker-compose.yml。
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from erp_core import SCHEMA_VERSION, dispatch, init_db

ROOT = os.path.dirname(os.path.abspath(__file__))
PUBLIC = os.path.join(ROOT, 'public')
GITHUB = 'https://github.com/clawgeek/modern-erp-system'
STARTED = time.time()

MIME = {'.html': 'text/html; charset=utf-8', '.js': 'application/javascript; charset=utf-8',
        '.css': 'text/css; charset=utf-8', '.json': 'application/json; charset=utf-8',
        '.svg': 'image/svg+xml', '.png': 'image/png', '.ico': 'image/x-icon'}


class Handler(BaseHTTPRequestHandler):
    server_version = 'YunshuERP/1.0'
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args):
        sys.stdout.write('[%s] %s\n' % (time.strftime('%H:%M:%S'), fmt % args))
        sys.stdout.flush()

    def _send(self, status, payload, ctype='application/json; charset=utf-8'):
        data = payload if isinstance(payload, bytes) else json.dumps(
            payload, ensure_ascii=False, default=str).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Idempotency-Key')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self._send(204, b'')

    def do_GET(self):
        path = urlparse(self.path).path
        if path.startswith('/api/'):
            return self._api('GET', path, {})
        return self._static(path)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            n = int(self.headers.get('Content-Length') or 0)
            body = json.loads(self.rfile.read(n).decode('utf-8')) if n else {}
        except Exception:
            return self._send(400, {'error': '请求体不是合法 JSON'})
        return self._api('POST', path, body)

    def _api(self, method, path, body):
        try:
            payload, status = dispatch(method, path, body, dict(self.headers.items()))
            if status >= 400:
                self.log_message('%s %s -> %s', method, path, status)
            return self._send(status, payload)
        except Exception as exc:
            traceback.print_exc()
            return self._send(500, {'error': '服务器内部错误', 'detail': str(exc)})

    def _static(self, path):
        if path in ('', '/'):
            path = '/index.html'
        rel = os.path.normpath(path.lstrip('/')).replace('\\', '/')
        if rel.startswith('..') or rel.startswith('/'):
            return self._send(403, {'error': 'forbidden'})
        fp = os.path.join(PUBLIC, rel)
        if not os.path.isfile(fp):
            return self._send(404, {'error': 'not found: ' + path})
        ext = os.path.splitext(fp)[1].lower()
        with open(fp, 'rb') as f:
            data = f.read()
        self._send(200, data, MIME.get(ext) or mimetypes.guess_type(fp)[0] or 'application/octet-stream')


def main():
    ap = argparse.ArgumentParser(description='云枢 ERP 服务')
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=8766)
    ap.add_argument('--reset', action='store_true', help='重置数据库')
    args = ap.parse_args()

    db = init_db(reset=args.reset)
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    srv.daemon_threads = True
    print('=' * 66)
    print('  云枢 ERP  Yunshu ERP   v%s' % SCHEMA_VERSION)
    print('  数据库  : %s' % db)
    print('  服务地址: http://%s:%d' % (args.host, args.port))
    print('  健康检查: http://%s:%d/api/health' % (args.host, args.port))
    print('  开源地址: %s' % GITHUB)
    print('  按 Ctrl+C 停止服务')
    print('=' * 66)
    sys.stdout.flush()
    try:
        srv.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        print('\n服务已停止')
    finally:
        srv.server_close()


if __name__ == '__main__':
    main()
