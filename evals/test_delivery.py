"""Isolated delivery regression tests; no network calls or public submissions."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from agents import craftsman

class DeliveryTests(unittest.TestCase):
    def exercise(self, body, result, dry_run=False):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'db'
            c=sqlite3.connect(path)
            c.executescript("CREATE TABLE messages(id INTEGER,body TEXT,created_at TEXT,topic TEXT,consumed_at TEXT); CREATE TABLE bounties(url TEXT,repo TEXT,title TEXT,amount_usd REAL,status TEXT,fit_score REAL);")
            c.execute("INSERT INTO messages VALUES (1,?,'now','resolution',NULL)",(body,))
            c.executemany("INSERT INTO bounties VALUES (?,?,?,?,?,?)",[('https://github.com/right/repo/issues/7','right/repo','Fix',5,'attempted',1),('https://github.com/wrong/repo/issues/8','wrong/repo','Wrong',99,'attempted',100)])
            c.commit();c.close()
            with patch.object(craftsman,'connect',side_effect=lambda:sqlite3.connect(path)), patch.object(craftsman,'_con',side_effect=lambda:sqlite3.connect(path)), patch.object(craftsman.guard,'check_action'), patch.object(craftsman.bus,'broadcast'), patch.object(craftsman,'deliver',return_value=result) as deliver:
                craftsman.fulfil(dry_run=dry_run)
                c=sqlite3.connect(path)
                consumed=c.execute('SELECT consumed_at FROM messages').fetchone()[0]
                c.close()
                return consumed, deliver.call_args

    def test_failure_keeps_input(self):
        consumed,call=self.exercise('https://github.com/right/repo/issues/7\nФАЙЛ: guide.md\nContent',{'ok':False})
        self.assertIsNone(consumed)
        self.assertEqual(call.args[0],'right/repo')

    def test_preview_keeps_input(self):
        consumed,_=self.exercise('https://github.com/right/repo/issues/7\nФАЙЛ: guide.md\nContent',{'ok':True,'dry_run':True},True)
        self.assertIsNone(consumed)

    def test_confirmed_pr_acknowledges(self):
        consumed,_=self.exercise('https://github.com/right/repo/issues/7\nФАЙЛ: guide.md\nContent',{'ok':True,'url':'https://github.com/right/repo/pull/9'})
        self.assertIsNotNone(consumed)

    def test_missing_target_never_guesses(self):
        consumed,call=self.exercise('ФАЙЛ: guide.md\nContent',{'ok':True})
        self.assertIsNone(consumed)
        self.assertIsNone(call)

    def test_missing_file_keeps_input(self):
        consumed,call=self.exercise('Please do good work',{'ok':True})
        self.assertIsNone(consumed)
        self.assertIsNone(call)

if __name__=='__main__': unittest.main()
