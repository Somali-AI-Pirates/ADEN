import json
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest


# 실제 설치 명령과 앱 팩토리를 실행하되 포트 연결·외부 자원 준비는 하지 않는다.
CONSOLE_PROBE = """
import json
import logging
from importlib.metadata import distribution
import sys
from unittest.mock import patch

import uvicorn
from fastapi.testclient import TestClient

def inspect_server(app, **options):
    config = uvicorn.Config(app, **options)
    config.load()
    # context manager에 진입하지 않아 lifespan은 실행하지 않는다.
    client = TestClient(config.loaded_app)
    try:
        response = client.get('/health')
    finally:
        client.close()
    logging.getLogger('uvicorn.error').info('uvicorn_cli_probe')
    logging.getLogger('other_library').info('suppressed_library_probe')
    print(json.dumps({
        'target': app,
        'factory': config.factory,
        'reload': config.reload,
        'status_code': response.status_code,
        'query_ready': response.json()['query_ready'],
    }))

command = sys.argv[1]
entry_point = next(
    point for point in distribution('aden-shipping-rag').entry_points
    if point.group == 'console_scripts' and point.name == command
)
sys.argv = [command]
with patch('uvicorn.run', side_effect=inspect_server):
    entry_point.load()()
"""


class CliTests(unittest.TestCase):
    def test_installed_commands_resolve_factory_and_configure_fresh_process_logs(self):
        with TemporaryDirectory() as directory:
            for command, reload in [("server", False), ("dev", True)]:
                with self.subTest(command=command):
                    result = subprocess.run(
                        [sys.executable, "-I", "-B", "-c", CONSOLE_PROBE, command],
                        cwd=directory,
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(json.loads(result.stdout), {
                        "target": "app.api.app:create_app",
                        "factory": True,
                        "reload": reload,
                        "status_code": 200,
                        "query_ready": False,
                    })
                    self.assertIn("INFO app.api.middleware http_request", result.stderr)
                    self.assertIn("path=/health status_code=200", result.stderr)
                    self.assertIn("uvicorn_cli_probe", result.stderr)
                    self.assertNotIn("suppressed_library_probe", result.stderr)
