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

    def test_structured_provider_response(self):
        answer = {"tool":"supply_check", "why":"check available sources"}
        with patch.dict(os.environ, ENV, clear=True), patch('urllib.request.urlopen',return_value=io.BytesIO(json.dumps({"success":True,"result":{"response":answer}}).encode())) as call:
            self.assertEqual(json.loads(router.run('classify','choose a tool')),answer)
            self.assertEqual(json.loads(call.call_args.args[0].data)['response_format'],{'type':'json_object'})

    def test_cloud_rotation_persists_and_cap_survives_restart(self):
        import sqlite3
        import tempfile
        from pathlib import Path
        from ops import cloud_turn
        from datetime import datetime, timezone
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'test.db'
            actor=Mock()
            actor.act.return_value={"ok":True,"detail":"TEST ONLY"}
            with patch.dict(os.environ,ENV,clear=True), patch('core.db.init'), patch('core.db.connect',side_effect=lambda:sqlite3.connect(path)), patch('core.roster.wire',return_value={'a':None,'b':None}), patch('core.agent.get',return_value=actor) as get, patch('core.recall.recall'), patch('builtins.print'):
                self.assertEqual(cloud_turn.main(),0)
                self.assertEqual(cloud_turn.main(),0)
                self.assertEqual([c.args[0] for c in get.call_args_list],['a','b'])
                with sqlite3.connect(path) as con:
                    con.executemany('INSERT INTO cloud_turns(agent,started_at) VALUES (?,?)',[('a',datetime.now(timezone.utc).isoformat())]*94)
                self.assertEqual(cloud_turn.main(),0)
                con.close()
                self.assertEqual(actor.act.call_count,2)

if __name__ == '__main__': unittest.main()
