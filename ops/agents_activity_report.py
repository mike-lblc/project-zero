"""ВЕДОМОСТЬ АГЕНТОВ — страница из таблицы активности и заметок дня.

    py -3.13 -X utf8 ops/agents_activity_report.py activity.json notes.json docs/AGENTS_ACTIVITY_<date>.html

activity.json — вывод `ops/agents_activity.py --json`; notes.json — заметки дня
(что изменилось, что найдено, что открыто у владельца, что проверить). Страница
без внешних скриптов, шрифты с Google Fonts, обе темы.
"""
from __future__ import annotations

import html
import json
import sys
from collections import Counter
from pathlib import Path

VERDICT = {
    "ДЕЙСТВУЕТ": ("act", "след снаружи за сутки: обращение, пост, правка, PR"),
    "ИЩЕТ": ("seek", "новые находки, наружу не выходил"),
    "ДЕЖУРИТ": ("watch", "обороты с разными исходами, без находок и действий"),
    "ВХОЛОСТУЮ": ("idle", "обороты с одним и тем же исходом"),
    "МОЛЧИТ": ("silent", "за сутки ни одного оборота"),
    "ТОЛЬКО СУЩЕСТВУЕТ": ("silent", "за неделю ни оборота, ни следа"),
}


def e(x):
    return html.escape(str(x if x is not None else ""))


def build(rows, notes):
    counts = Counter(r["verdict"] for r in rows)
    tiles = "".join(
        f'<div class="tile v-{VERDICT[v][0]}"><div class="tile-n">{counts.get(v, 0)}</div>'
        f'<div class="tile-v"><span class="dot"></span>{e(v)}</div><div class="tile-d">{e(VERDICT[v][1])}</div></div>'
        for v in VERDICT if v != "ТОЛЬКО СУЩЕСТВУЕТ" or counts.get(v))
    trs = []
    for r in rows:
        cls = VERDICT[r["verdict"]][0]
        trs.append(
            f'<tr><td class="name">{e(r["agent"])}<span class="kind">{e(r["kind"])}</span></td>'
            f'<td><span class="chip v-{cls}"><span class="dot"></span>{e(r["verdict"])}</span></td>'
            f'<td class="num">{r["runs_24h"]}<span class="sub">{r["ok_24h"]} ок · {r["distinct_24h"]} разн.</span></td>'
            f'<td class="num">{r["findings_24h"]}</td><td class="num">{r["decisions_24h"]}</td>'
            f'<td class="num">{r["said_24h"]}</td><td class="num strong">{r["external_24h"]}</td>'
            f'<td class="num">{r["runs_7d"]}<span class="sub">{r["findings_7d"]} нах. · {r["external_7d"]} внеш.</span></td>'
            f'<td class="mono">{e(r["last_run"]) or "—"}</td><td class="step">{e(r["last_step"][:70]) or "—"}</td></tr>')
    changes = "".join(f"<li><b>{e(t)}</b> {e(d)}</li>" for t, d in notes["changes"])
    found = "".join(f"<li><b>{e(t)}</b> {e(d)}</li>" for t, d in notes["found"])
    open_ = "".join(f"<li>{e(x)}</li>" for x in notes["open"])
    check = "".join(f"<li>{e(x)}</li>" for x in notes["check"])
    return f"""<title>Ведомость агентов P0</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Condensed:wght@500;600&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
:root{{--ground:#EEF1F4;--surface:#FFFFFF;--ink:#16202A;--muted:#5B6875;--line:#D5DBE2;--accent:#B4531B;
--act:#1F7A4D;--seek:#2B5FB0;--watch:#6A737D;--idle:#B7791F;--silent:#B23A3A;--tint:#F6F8FA}}
@media (prefers-color-scheme: dark){{:root:not([data-theme="light"]){{--ground:#0F141A;--surface:#171E27;--ink:#E6EBF0;--muted:#9AA7B4;--line:#2A3440;--accent:#E0763A;
--act:#4CC38A;--seek:#7AA7F0;--watch:#98A2AD;--idle:#E0B15A;--silent:#F08A8A;--tint:#1C2530}}}}
:root[data-theme="dark"]{{--ground:#0F141A;--surface:#171E27;--ink:#E6EBF0;--muted:#9AA7B4;--line:#2A3440;--accent:#E0763A;
--act:#4CC38A;--seek:#7AA7F0;--watch:#98A2AD;--idle:#E0B15A;--silent:#F08A8A;--tint:#1C2530}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--ground);color:var(--ink);font:15px/1.5 "IBM Plex Sans",system-ui,sans-serif;padding-block:32px 56px;padding-inline:20px}}
.wrap{{max-width:1160px;margin:0 auto;display:grid;gap:28px}}
h1,h2{{font-family:"IBM Plex Sans Condensed","IBM Plex Sans",sans-serif;font-weight:600;letter-spacing:-.01em;text-wrap:balance;margin:0}}
h1{{font-size:38px;line-height:1.05}} h2{{font-size:22px;margin-bottom:12px}}
.eyebrow{{font:500 12px/1 "IBM Plex Mono",monospace;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin-bottom:10px}}
header{{border-bottom:3px solid var(--accent);padding-bottom:20px;display:grid;gap:12px}}
header p{{max-width:68ch;margin:0;font-size:17px}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px}}
.tile{{background:var(--surface);border:1px solid var(--line);padding:14px 16px;display:grid;gap:4px}}
.tile-n{{font:500 34px/1 "IBM Plex Mono",monospace;font-variant-numeric:tabular-nums}}
.tile-v{{font-weight:600;display:flex;align-items:center;gap:8px}}
.tile-d{{color:var(--muted);font-size:13px}}
.dot{{width:10px;height:10px;border-radius:50%;display:inline-block;background:currentColor;flex:none}}
.v-act .tile-v,.chip.v-act{{color:var(--act)}} .v-seek .tile-v,.chip.v-seek{{color:var(--seek)}}
.v-watch .tile-v,.chip.v-watch{{color:var(--watch)}} .v-idle .tile-v,.chip.v-idle{{color:var(--idle)}} .v-silent .tile-v,.chip.v-silent{{color:var(--silent)}}
.chip{{display:inline-flex;align-items:center;gap:6px;font-weight:600;font-size:13px;white-space:nowrap}}
.tablewrap{{overflow-x:auto;background:var(--surface);border:1px solid var(--line)}}
table{{border-collapse:collapse;width:100%;min-width:980px;font-size:14px}}
th{{position:sticky;top:0;background:var(--tint);text-align:left;font:500 11px/1.3 "IBM Plex Mono",monospace;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);padding:10px 12px;border-bottom:1px solid var(--line)}}
td{{padding:9px 12px;border-bottom:1px solid var(--line);vertical-align:top}}
tr:last-child td{{border-bottom:0}}
td.num{{font-family:"IBM Plex Mono",monospace;font-variant-numeric:tabular-nums;text-align:right;white-space:nowrap}}
td.strong{{font-weight:500;color:var(--accent)}}
.sub{{display:block;color:var(--muted);font-size:11px;font-family:"IBM Plex Sans",sans-serif}}
td.name{{font-weight:600;white-space:nowrap}} .kind{{display:block;color:var(--muted);font-weight:400;font-size:11px}}
td.mono{{font-family:"IBM Plex Mono",monospace;white-space:nowrap;color:var(--muted)}}
td.step{{color:var(--muted);max-width:340px}}
.cols{{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:24px}}
.panel{{background:var(--surface);border:1px solid var(--line);padding:18px 20px}}
ul{{margin:0;padding-left:18px;display:grid;gap:8px}} li b{{font-weight:600}}
.note{{color:var(--muted);font-size:13px;max-width:80ch}}
.commit{{font-family:"IBM Plex Mono",monospace;font-size:13px;color:var(--muted)}}
a{{color:var(--accent)}} :focus-visible{{outline:2px solid var(--accent);outline-offset:2px}}
@media (max-width:520px){{h1{{font-size:30px}} body{{padding-inline:16px}}}}
</style>
<div class="wrap">
<header>
  <div class="eyebrow">P0 · {e(notes["date"])} · {e(notes["cut"])}</div>
  <h1>Ведомость агентов P0</h1>
  <p>{e(notes["thesis"])}</p>
</header>
<section>
  <div class="eyebrow">Итог по вердиктам</div>
  <div class="tiles">{tiles}</div>
</section>
<section>
  <h2>Кто что делал</h2>
  <div class="tablewrap"><table>
  <thead><tr><th>Агент</th><th>Вердикт</th><th>Обороты 24ч</th><th>Находки</th><th>Решения</th><th>Реплики</th><th>Наружу</th><th>7 дней</th><th>Последний оборот (UTC)</th><th>Последний шаг</th></tr></thead>
  <tbody>{''.join(trs)}</tbody></table></div>
  <p class="note">Считается из базы (runs, evidence, agent_decisions, messages, outreach, dealer_attempts, moltbook_receipts, code_fixes, pull_requests, proof_of_work), не из самоотчётов агентов. Одно решение рассуждающего за сутки — это очередь дала слово, а не усилие; усилием считается след наружу или новая находка. Служебные сообщения сторожа и уведомления о паузах не считаются ни находками, ни репликами.</p>
</section>
<div class="cols">
  <section class="panel"><h2>Что изменилось сегодня</h2><ul>{changes}</ul></section>
  <section class="panel"><h2>Что найдено и починено</h2><ul>{found}</ul></section>
</div>
<div class="cols">
  <section class="panel"><h2>Открыто у владельца</h2><ul>{open_}</ul></section>
  <section class="panel"><h2>Что проверить через час</h2><ul>{check}</ul></section>
</div>
<p class="commit">{e(notes["commit"])}</p>
</div>
"""


if __name__ == "__main__":
    src, notes_p, out = sys.argv[1:4]
    rows = json.loads(Path(src).read_text(encoding="utf-8"))
    notes = json.loads(Path(notes_p).read_text(encoding="utf-8"))
    Path(out).write_text(build(rows, notes), encoding="utf-8")
    print("written", out, len(rows), "rows")
