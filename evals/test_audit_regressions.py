import json, sqlite3, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from core import db, events, regressions
from agents import executor, worker
class AuditRegressions(unittest.TestCase):
    def test_persisted_invariants_restore_into_a_fresh_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = root / 'invariants.json'
            store.write_text(json.dumps([{
                'name': 'test-only persisted lesson',
                'kind': 'present',
                'target': 'core/regressions.py',
                'expr': 'def restore',
                'origin': 'A clean cloud runner previously discarded learned checks.',
            }]), encoding='utf-8')
            db._SCHEMA_DONE.clear()
            with patch.object(db, 'DB_PATH', root / 'brain.db'), \
                    patch.object(db, '_WAL_SET', False), \
                    patch.object(regressions, 'STORE', store):
                added, detail = regressions.restore()
                self.assertEqual(added, 1, detail)
                self.assertEqual(regressions.count(), 1)
            db._SCHEMA_DONE.clear()

    def test_event_payload_roundtrip_and_claim_exclusion(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'db'
            c=sqlite3.connect(path);c.executescript(events.SCHEMA);c.close()
            with patch.object(events,'_con',side_effect=lambda:sqlite3.connect(path)):
                payload={"text":"x"*3000}
                eid,_=events.publish('fresh_bounty',payload,'test')
                self.assertEqual(json.loads(events.pending()[0]['payload']),payload)
                self.assertTrue(events.claim(eid,'bounty'))
                self.assertFalse(events.claim(eid,'another'))
                self.assertEqual(events.pending(),[])
                events.release(eid)
                self.assertEqual(len(events.pending()),1)
                with self.assertRaises(ValueError):events.publish('fresh_bounty',{'text':'x'*70000})
    def test_failed_turn_not_success(self):
        with patch.object(worker,'should_run',return_value=True),patch.object(worker,'record_run'),patch.object(worker,'note_result',return_value=0),patch.object(worker,'say'),patch.object(worker.telemetry,'span'),patch('builtins.print'):
            def fail():raise RuntimeError('TEST ONLY')
            self.assertIsNone(worker._turn('test',fail,'test',0))
    def test_executor_rejects_vacuous_success(self):
        self.assertFalse(executor.verify('nothing',[],{})[0])
    def test_executor_rejects_changed_arguments(self):
        facts=[{'full':'goal progress','name':'progress','required':['id','value'],'optional':[],'source':'cli.py:1'}]
        text=executor.compose(facts,'Test')['markdown']
        self.assertTrue(executor.verify(text,facts,{'cli.py':'source'})[0])
        self.assertFalse(executor.verify(text.replace('<value>',''),facts,{'cli.py':'source'})[0])
if __name__=='__main__':unittest.main()
