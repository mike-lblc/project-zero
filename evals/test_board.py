"""ОБЩАЯ ДОСКА И РАЗГОВОР АГЕНТОВ (владелец 15.09: думать должна вся экосистема).

Что закреплено.
- Каждый агент видит на доске находки и слова других, вопросы к себе, общий план и деньги.
- Вопрос одного агента появляется у адресата; ответ возвращается спрашивавшему.
- Любой агент может предложить путь в общий план; дубль не создаётся.
- Улучшатель отвергает ухудшения до записи: снятый предел чтения, убранный таймаут, новое
  неопределённое имя.

База временная, сеть и модель не трогаются.
"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class TempDB(unittest.TestCase):
    def setUp(self):
        from core import db, execution, guard
        self.db, self.ex, self.guard = db, execution, guard
        self.saved = (db.DB_PATH, set(db._SCHEMA_DONE), db._WAL_SET, execution._MIGRATED[0], guard.check_action)
        self.tmp = tempfile.TemporaryDirectory()
        db.DB_PATH = Path(self.tmp.name) / "board.db"
        db._SCHEMA_DONE.clear(); db._WAL_SET = False; execution._MIGRATED[0] = False
        guard.check_action = lambda *a, **k: None
        db.init().close(); execution._con().close()

    def tearDown(self):
        (self.db.DB_PATH, done, self.db._WAL_SET, self.ex._MIGRATED[0], self.guard.check_action) = self.saved
        self.db._SCHEMA_DONE.clear(); self.db._SCHEMA_DONE.update(done)
        import gc; gc.collect()
        self.tmp.cleanup()


class SharedMind(TempDB):
    def test_board_shows_others_and_questions_flow_back(self):
        from core import board, bus, roster  # noqa: F401  (roster заполняет справочник адресатов)
        roster.wire()
        c = self.db.connect()
        sid = c.execute("INSERT INTO sources(url,title,fetched_at,raw_excerpt) VALUES (?,?,?,?)",
                        ("worker://dealer", "worker cycle dealer", "2026-09-15T10:00:00+00:00", "")).lastrowid
        c.execute("INSERT INTO evidence(claim,source_id,agent,created_at,confidence) VALUES (?,?,?,?,?)",
                  ("x402scan: 18 511 плательщиков за 30 дней", sid, "dealer", "2026-09-15T10:00:00+00:00", 0.9))
        c.commit(); c.close()
        bus.broadcast("leads", "лидов с публичным каналом: 273")
        qid = bus.ask("dealer", "leads", "у кого из 273 лидов есть кошелёк в issue?")
        b_leads = board.view("leads")
        self.assertTrue(any("18 511" in x for x in b_leads["находки других"]))
        self.assertTrue(any(f"#{qid}" in x for x in b_leads["вопросы ко мне"]), b_leads["вопросы ко мне"])
        self.assertFalse(any("лидов с публичным каналом" in x for x in b_leads["что говорят другие"]),
                         "свои слова на доске не повторяются")
        bus.answer("leads", qid, "у троих: см. leads.note")
        b_dealer = board.view("dealer")
        self.assertTrue(any("у троих" in x for x in b_dealer["ответы мне"]))
        self.assertIn("деньги", b_dealer)

    def test_any_agent_can_propose_a_path_once(self):
        from core import roster
        from core.agent import TOOLS
        roster.wire()
        fn = TOOLS["propose_path"].fn
        first = fn("leads", "sell docs to x402 sellers with bounty labels", "board:github", "github_issue",
                   "docs", "bounty_claim", "25 объявленных наград на $1 430")
        second = fn("leads", "sell docs to x402 sellers with bounty labels", "board:github", "github_issue",
                    "docs", "bounty_claim", "25 объявленных наград на $1 430")
        self.assertIn("добавлена", first)
        self.assertIn("уже есть", second)
        from core import board
        plan = board.view("critic")["общий план (гипотезы)"]
        self.assertTrue(any("sell docs to x402 sellers" in p for p in plan))

    def test_every_agent_has_collaboration_tools(self):
        from core import roster
        for name, a in roster.wire().items():
            for t in ("ask_agent", "answer_agent", "handoff_to", "propose_path", "note_finding"):
                self.assertIn(t, a.tools, f"{name} без {t}")


class AnswerFirst(TempDB):
    """Вопрос соседа получает ответ ФАКТОМ до собственного хода агента — механической
    выжимкой из состояния, не суждением; модель здесь подменена."""

    def test_pending_question_is_answered_before_acting(self):
        from core import bus, roster, router, agent as agent_core
        roster.wire()
        qid = bus.ask("watchdog", "leads", "Ты молчишь час. Что ты делаешь для первого платежа?")
        saved = router.run
        router.run = lambda task, prompt, **k: "Последним проверял каналы 60 лидов; кошелёк известен у троих; мешает отсутствие ответов."
        try:
            n = agent_core.get("leads").answer_pending()
        finally:
            router.run = saved
        self.assertEqual(n, 1)
        answers = bus.answers_for("watchdog", 3)
        self.assertTrue(any("60 лидов" in a["answer"] for a in answers), answers)
        self.assertEqual(bus.pending_questions("leads"), [])
        c = self.db.connect()
        chose = c.execute("SELECT chose FROM agent_decisions WHERE agent='leads' ORDER BY id DESC LIMIT 1").fetchone()
        c.close()
        self.assertEqual(chose[0], "answer_agent")


class ImproverGuards(unittest.TestCase):
    def test_degradations_are_named(self):
        from agents import improver
        self.assertIn("снят предел чтения ответа", improver.degrades("r.read(HEAD_BYTES)", "r.read()"))
        self.assertIn("убран таймаут сетевого вызова", improver.degrades("get(u, timeout=20)", "get(u)"))
        self.assertIn("обработка отказа заменена на pass",
                      improver.degrades("try:\n    x()\nexcept Exception:\n    raise\n", "try:\n    x()\nexcept Exception:\n    pass\n"))
        self.assertEqual(improver.degrades("a = 1\nb = 2\n", "a = 1\nb = 3\n"), [])

    def test_new_undefined_name_is_caught(self):
        from agents import improver
        fresh = improver.new_static_problems("import os\nx = os.sep\n", "x = os.sep\n")
        self.assertTrue(any("undefined name" in f for f in fresh), fresh)


if __name__ == "__main__":
    unittest.main()
