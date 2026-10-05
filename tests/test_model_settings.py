from pathlib import Path
import tempfile,unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import create_app
from app.llm import _runtime

class ModelSettingsTests(unittest.TestCase):
    def test_settings_redact_key_and_do_not_call_provider(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict(_runtime,{},clear=True),patch('app.llm.httpx.Client') as provider:
            client=TestClient(create_app(Path(folder)))
            fixture='-'.join(['synthetic','placeholder','only'])
            self.assertEqual(client.post('/api/model/settings',json={'base_url':'https://api.example.com/v1','model':'test-model','api_key':fixture}).status_code,200)
            self.assertTrue(client.get('/api/model/settings').json()['configured'])
            self.assertNotIn(fixture,client.get('/api/model/settings').text)
            self.assertEqual(client.post('/api/model/settings',json={'base_url':'http://untrusted.example/v1','model':'test'}).status_code,400)
            provider.assert_not_called()
