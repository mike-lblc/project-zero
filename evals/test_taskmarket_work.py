"""TASKMARKET БЕЗ ОЖИДАНИЯ: ворота снимаются из брифа, работа проверяется механически.

Модель и сеть подменены. Три реальных брифа 15.09 и наши три поданные работы —
эталон: работы обязаны проходить ворота своих брифов.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from agents import taskmarket_work as tw  # noqa: E402

HEAT = ("Conduction, Convection, Radiation: Three Routes for Heat\n\nDeliverable\nOne UTF-8 Markdown file, any descriptive "
        ".md filename, 300-450 words excluding source URLs, at most 12,000 bytes.\n\nRequirements\n6. Map at least one working "
        "authoritative source URL to each section")
CSV = ("Five Writing Systems and How Their Lines Flow\n\nDeliverable\nOne UTF-8 CSV, any descriptive .csv filename, exactly five "
       "data rows and at most 12,000 bytes. Use this exact header:\nscript_name,usual_horizontal_direction,vertical_use,"
       "important_qualification,source_url\n\nRequirements\n5. Map one working authoritative source URL to each row")
HTML = ("A Tiny Aspect-Ratio Reducer\n\nDeliverable\nOne self-contained UTF-8 HTML file, any descriptive .html filename, at most "
        "22,000 bytes.\n\nNegative scope\nNo external libraries/fonts/assets")


class Gates(unittest.TestCase):
    def setUp(self):
        self._alive = tw._alive
        tw._alive = lambda u: True          # сеть подменена: живость URL проверяется отдельно

    def tearDown(self):
        tw._alive = self._alive

    def test_gates_are_read_from_the_briefs(self):
        g = tw.gates_from_brief(HEAT)
        self.assertEqual((g["kind"], g["min_words"], g["max_words"], g["max_bytes"], g["sources"]), ("markdown", 300, 450, 12000, True))
        g = tw.gates_from_brief(CSV + "\n6. Across the three descriptive fields, use at most 80 words per row.")
        self.assertEqual((g["kind"], g["rows"], g["max_bytes"], g["max_words_per_row"], g["max_words"]), ("csv", 5, 12000, 80, None))
        self.assertTrue(g["header"].startswith("script_name,usual_horizontal_direction"))
        g = tw.gates_from_brief(HTML)
        self.assertEqual((g["kind"], g["max_bytes"], g["self_contained"]), ("html", 22000, True))

    def test_our_submitted_files_pass_their_gates(self):
        pairs = [("0xedb3d76f/three-routes-for-heat.md", HEAT), ("0x51ac50cf/five-writing-systems-direction.csv", CSV),
                 ("0x06cd0c0d/aspect-ratio-reducer.html", HTML)]
        for rel, brief in pairs:
            text = (ROOT / "work" / "taskmarket" / rel).read_text(encoding="utf-8")
            self.assertEqual(tw.check(text, tw.gates_from_brief(brief)), [], rel)

    def test_violations_are_named(self):
        g = tw.gates_from_brief(CSV)
        bad = ("wrong,header,columns,in,file\n"
               "Latin,left to right,not conventional,qualification text here,https://www.w3.org/International/questions/qa-scripts\n")
        probs = tw.check(bad, g)
        self.assertTrue(any("заголовком" in p for p in probs), probs)
        self.assertTrue(any("ровно 5" in p for p in probs), probs)
        g = tw.gates_from_brief(HTML)
        probs = tw.check('<html><head><script src="https://cdn.example.com/x.js"></script></head><body></body></html>', g)
        self.assertTrue(any("внешние" in p for p in probs), probs)
        g = tw.gates_from_brief(HEAT)
        probs = tw.check("A short paragraph about heat that names no sources at all and is far too brief for the brief.", g)
        self.assertTrue(any("не меньше 300" in p for p in probs) and any("источники" in p for p in probs), probs)

    def test_dead_source_url_fails_the_gate(self):
        tw._alive = lambda u: False
        probs = tw.check("word " * 320 + " https://example.org/dead", tw.gates_from_brief(HEAT))
        self.assertTrue(any("недоступные URL" in p for p in probs), probs)


class Drafting(unittest.TestCase):
    def test_fences_are_stripped_and_draft_route_is_mechanical(self):
        from core import router
        self.assertIn("draft", router.MECHANICAL)
        saved = router.run
        router.run = lambda task, prompt, **k: "```csv\nscript_name,a\nLatin,x\n```"
        try:
            out = tw.draft(CSV, tw.gates_from_brief(CSV))
        finally:
            router.run = saved
        self.assertEqual(out, "script_name,a\nLatin,x")

    def test_review_parses_model_json(self):
        from core import router
        saved = router.run
        router.run = lambda task, prompt, **k: 'Result: {"ok": false, "problems": ["row 3 has no source"]}'
        try:
            self.assertEqual(tw.review(CSV, "x"), ["row 3 has no source"])
            router.run = lambda task, prompt, **k: '{"ok": true, "problems": []}'
            self.assertEqual(tw.review(CSV, "x"), [])
        finally:
            router.run = saved


if __name__ == "__main__":
    unittest.main()
