"""ВОРОНКА И ПУТЬ ПЛАТЕЖА — что закреплено после чтения 16.09.

- Владение каналом: репозиторий принимается только по домашней странице или владельцу, не по
  упоминанию домена в описании (случай yfinance / coinmarketcap-exporter).
- Письмо продавца содержит цену и способ оплаты и не обещает исчезнуть.
- Закрывающий отвечает сам: отказ и закрытое обсуждение закрывают сделку без реплики; интерес
  даёт реплику по шаблону с ценой; бот-комментарий не от участника — шум.
- pursue не снимает задачи площадок как «репозиторий не найден».
- Taskmarket: баланс читается по ключам CLI; фильтр пропускает «text report»; требование
  источников распознаётся по «official HTTPS sources».
Сеть, gh и модель подменены.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class Ownership(unittest.TestCase):
    def test_description_mention_no_longer_grants_ownership(self):
        from agents import leads
        import subprocess
        saved = subprocess.run
        class R:  # noqa: D401
            returncode = 0
            stdout = ('{"hasIssuesEnabled": true, "homepageUrl": "https://example.org", '
                      '"description": "exporter for laso.finance data", "owner": {"login": "bonovoxly"}}')
        subprocess.run = lambda *a, **k: R()
        try:
            self.assertFalse(leads._accept_repo("laso.finance", "laso", "bonovoxly/exporter"))
            R.stdout = '{"hasIssuesEnabled": true, "homepageUrl": "https://laso.finance", "description": "", "owner": {"login": "x"}}'
            self.assertTrue(leads._accept_repo("laso.finance", "laso", "x/laso-app"))
            R.stdout = '{"hasIssuesEnabled": true, "homepageUrl": "", "description": "", "owner": {"login": "laso-finance"}}'
            self.assertTrue(leads._accept_repo("laso.finance", "laso", "laso-finance/api"))
        finally:
            subprocess.run = saved


class OutreachAsk(unittest.TestCase):
    def test_message_carries_price_payment_path_and_invites_reply(self):
        from agents import outreach
        text = outreach.compose(("agent402.tools", 3, 1200, 158, 0.01, 40), {"место": 12, "всего служб": 1946})
        self.assertIn("$0.01 per query", text)
        self.assertIn("/pay", text)
        self.assertIn("reply here", text)
        self.assertNotIn("I only send this once", text)
        self.assertIn("BTC, ETH, SOL and TRX", text)


class CloserReplies(unittest.TestCase):
    def test_template_reply_names_price_and_payment_paths(self):
        from agents import closer
        t = closer._reply_text("blockrun.ai", "interested")
        self.assertIn("search?q=blockrun", t)
        self.assertIn("$0.01", t)
        self.assertIn("/pay", t)
        self.assertNotIn("Claude", t); self.assertNotIn("AI", t.replace("AI ", "").replace("PAY", ""))

    def test_intent_classification_maps_model_words(self):
        from agents import closer
        from core import router
        saved = router.run
        try:
            router.run = lambda task, prompt, **k: "Intent: declined."
            self.assertEqual(closer._classify_reply("no thanks"), "declined")
            router.run = lambda task, prompt, **k: "interested"
            self.assertEqual(closer._classify_reply("sounds useful, how?"), "interested")
            router.run = lambda task, prompt, **k: (_ for _ in ()).throw(RuntimeError("down"))
            self.assertEqual(closer._classify_reply("x"), "other")
        finally:
            router.run = saved


class PursueNonGithub(unittest.TestCase):
    def test_non_github_bounty_is_not_marked_lost(self):
        import inspect
        from agents import craftsman
        src = inspect.getsource(craftsman.pursue)
        self.assertIn("не GitHub — ведётся своим шагом", src)
        # снятие как «репозиторий не найден» стоит ПОСЛЕ проверки, что ссылка — GitHub
        self.assertLess(src.index("не GitHub — ведётся своим шагом"), src.index("не найден (404)"))


class TaskmarketIntake(unittest.TestCase):
    def test_sources_requirement_is_detected_in_kai_style_brief(self):
        from agents import taskmarket_work as tw
        g = tw.gates_from_brief("Submit one original English text report, at most 650 whitespace-separated words, "
                                "with one to four publicly readable official HTTPS sources.")
        self.assertTrue(g["sources"]); self.assertEqual(g["max_words"], 650)

    def test_balance_keys_follow_the_cli(self):
        import inspect
        from agents import bounty
        src = inspect.getsource(bounty.taskmarket_sync)
        self.assertIn('"balanceUsdc"', src); self.assertIn("balanceBaseUnits", src)
        self.assertIn('"text report"', src)
        self.assertNotIn('"testing"', src.split("NOT_OURS")[1].split(")")[0])


if __name__ == "__main__":
    unittest.main()
