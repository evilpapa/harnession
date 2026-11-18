import http.client
from http.server import HTTPServer
import json
import threading
import unittest

from harnession.web import handler


class FakeGateway:
    def dispatch(self, method, params):
        return dict(ok=method == 'bootstrap')


class WebBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.server = HTTPServer(('127.0.0.1', 0), handler(FakeGateway(), 'http://127.0.0.1:1420'))
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()

    def request(self, **headers):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=2)
        try:
            connection.request('POST', '/api/rpc', json.dumps(dict(method='bootstrap')),
                               headers={'Content-Type': 'application/json', **headers})
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def test_local_json_request_is_allowed(self):
        status, payload = self.request(Origin='http://127.0.0.1:1420')
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(payload)['result']['ok'])

    def test_foreign_origin_and_rebinding_host_are_rejected(self):
        self.assertEqual(self.request(Origin='https://untrusted.example')[0], 403)
        self.assertEqual(self.request(Host='untrusted.example')[0], 403)

    def test_form_content_cannot_invoke_local_rpc(self):
        self.assertEqual(self.request(**{'Content-Type': 'text/plain'})[0], 400)


if __name__ == '__main__':
    unittest.main()
