"""Loopback-only browser development API. PTY is available in Tauri only."""

import argparse
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from .core import data_dir
from .gateway import Gateway, response


def handler(gateway, allowed_origin):
    class Handler(BaseHTTPRequestHandler):
        def drain_body(self):
            # Windows 会在仍有未读请求体时关闭套接字产生 RST，先丢弃再回错。
            try:
                size = min(int(self.headers.get('Content-Length', '0')), 1 << 20)
            except ValueError:
                return
            try:
                while size > 0:
                    chunk = self.rfile.read(min(size, 65536))
                    if not chunk:
                        break
                    size -= len(chunk)
            except OSError:
                pass

        def do_POST(self):
            host = self.headers.get('Host', '')
            origin = self.headers.get('Origin')
            allowed = {allowed_origin, 'http://' + host}
            if self.path != '/api/rpc' or (origin and origin not in allowed) or host not in {
                '127.0.0.1:' + str(self.server.server_port), 'localhost:' + str(self.server.server_port),
                # Vite proxy preserves the source Host header.
                allowed_origin.removeprefix('http://')
            }:
                self.drain_body()
                self.send_error(403)
                return
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 65536 or self.headers.get_content_type() != 'application/json':
                    self.drain_body()
                    self.send_error(400)
                    return
                request = json.loads(self.rfile.read(size))
                payload = json.dumps(response(gateway, request), ensure_ascii=False).encode('utf8')
            except (ValueError, OSError):
                self.send_error(400)
                return
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            # Do not print user queries, source paths or message bodies.
            pass

    return Handler


def main():
    parser = argparse.ArgumentParser(description='Local Harnession development API')
    parser.add_argument('--db', type=Path, default=data_dir() / 'index.sqlite3')
    parser.add_argument('--port', type=int, default=8767)
    parser.add_argument('--origin', default='http://127.0.0.1:1420')
    args = parser.parse_args()
    if not args.origin.startswith(('http://127.0.0.1:', 'http://localhost:')):
        parser.error('只允许 loopback 开发来源')
    gateway = Gateway(args.db)
    server = HTTPServer(('127.0.0.1', args.port), handler(gateway, args.origin))
    print('Harnession development API on loopback port ' + str(args.port), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        gateway.close()


if __name__ == '__main__':
    main()
