"""Unauthorized clients must not occupy a worker while withholding a POST body."""
import socket
import tempfile
import threading
import unittest
from pathlib import Path

from inspection.http import make_server
from inspection.service import InspectionService


class RequestAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = InspectionService(Path(self.temp.name) / 'demo.db', demo=True)
        self.server = make_server('127.0.0.1', 0, self.service, token='synthetic-admission-token')
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.service.close()
        self.temp.cleanup()

    def response_without_body(self, extra=''):
        port = self.server.server_port
        with socket.create_connection(('127.0.0.1', port), timeout=.75) as client:
            client.sendall((f'POST /api/jobs HTTP/1.0\r\nHost: 127.0.0.1:{port}\r\n'
                            'Content-Type: application/json\r\nContent-Length: 4096\r\n' + extra + '\r\n').encode('ascii'))
            try:
                return client.recv(256).split(b'\r\n', 1)[0]
            except socket.timeout:
                self.fail('Request admission waited for an unauthorized body')

    def test_missing_token_rejected_before_reading_body(self):
        self.assertIn(b' 401 ', self.response_without_body())
        self.assertEqual(self.service.list_jobs()['total'], 0)

    def test_foreign_origin_rejected_before_reading_body(self):
        self.assertIn(b' 403 ', self.response_without_body('Origin: https://example.invalid\r\n'))
        self.assertEqual(self.service.list_jobs()['total'], 0)
