import io,json,sqlite3,unittest
from unittest.mock import patch
from ops import live_state
class LiveStateTests(unittest.TestCase):
    def test_public_payload_excludes_private_content(self):
        c=sqlite3.connect(':memory:')
        c.executescript("CREATE TABLE runs(id INTEGER,agent TEXT,started_at TEXT,ended_at TEXT,status TEXT,notes TEXT);CREATE TABLE payments(id INTEGER);CREATE TABLE spend(id INTEGER);CREATE TABLE messages(body TEXT);")
        c.execute("INSERT INTO runs VALUES(1,'executor','2026-09-12T00:00:00Z',NULL,'ok','PRIVATE_SECRET')")
        c.execute("INSERT INTO messages VALUES('private@example.com')")
        with patch.dict(live_state.os.environ,{'P0_CLOUDFLARE_AI_TOKEN':'TEST_TOKEN'}),patch.object(live_state.sqlite3,'connect',return_value=c),patch.object(live_state.urllib.request,'urlopen',return_value=io.BytesIO(b'{"success":true,"result":[{"success":true}]}')) as call:
            live_state.publish('produce_doc','executor','running')
            body=json.loads(call.call_args.args[0].data)
            state=json.loads(body['params'][0])
            self.assertEqual(state['status']['cloud_agent'],'executor')
            self.assertEqual(state['status']['payments'],0)
            self.assertNotIn('PRIVATE_SECRET',body['params'][0])
            self.assertNotIn('private@example.com',body['params'][0])
            self.assertNotIn('TEST_TOKEN',body['params'][0])
    def test_missing_credential_is_failure(self):
        with patch.dict(live_state.os.environ,{},clear=True):
            with self.assertRaises(RuntimeError):live_state.publish()
if __name__=='__main__':unittest.main()
