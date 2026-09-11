"""Offline contract tests. These do not claim provider connectivity."""
import io
import json
import os
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from core.cloud_model import generate, CloudModelUnavailable
from core import router

ENV = {"P0_MODEL_BACKEND": "cloudflare", "P0_CLOUD_FREE_VERIFIED": "1",
       "P0_CLOUDFLARE_ACCOUNT_ID": "a" * 32, "P0_CLOUDFLARE_AI_TOKEN": "test-only"}

class CloudModelTests(unittest.TestCase):
    def test_disabled_without_plan_verification(self):
        with patch.dict(os.environ, {}, clear=True), patch('urllib.request.urlopen') as call:
            with self.assertRaises(CloudModelUnavailable): generate('hello')
            call.assert_not_called()

    def test_cloud_route_never_calls_localhost(self):
        with patch.dict(os.environ, ENV, clear=True), patch('urllib.request.urlopen', return_value=io.BytesIO(json.dumps({"success": True, "result": {"response": "ready"}}).encode())) as call:
            self.assertEqual(router.run('classify', 'hello'), 'ready')
            self.assertTrue(call.call_args.args[0].full_url.startswith('https://api.cloudflare.com/'))
            self.assertEqual(call.call_count, 1)

    def test_quota_failure_is_not_retried_or_sent_to_localhost(self):
        with patch.dict(os.environ, ENV, clear=True), patch('urllib.request.urlopen', side_effect=HTTPError('test',429,'limit',{},None)) as call:
            with self.assertRaises(CloudModelUnavailable): router.run('classify','hello')
            self.assertEqual(call.call_count, 1)

    def test_success_http_with_invalid_body_is_failure(self):
        for data in ({"success":False}, {"success":True,"result":{}}, []):
            with self.subTest(data=data), patch.dict(os.environ, ENV, clear=True), patch('urllib.request.urlopen',return_value=io.BytesIO(json.dumps(data).encode())):
                with self.assertRaises(CloudModelUnavailable): generate('hello')

    def test_oversize_prompt_does_not_make_request(self):
        with patch.dict(os.environ, ENV, clear=True), patch('urllib.request.urlopen') as call:
            with self.assertRaises(CloudModelUnavailable): generate('x'*24001)
            call.assert_not_called()

    def test_existing_judgment_gate_is_preserved(self):
        with patch.dict(os.environ, ENV, clear=True), patch('urllib.request.urlopen') as call:
            with self.assertRaises(router.EscalationRequired): router.run('approve','hello')
            call.assert_not_called()

if __name__ == '__main__': unittest.main()
