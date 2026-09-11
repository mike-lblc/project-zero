"""Собирает страницу-снимок состояния, которую можно открыть с любого устройства.

Живой дашборд работает локально и по сети недоступен. Эта страница — СНИМОК:
все числа берутся из базы в момент сборки и подписаны как снимок, а не как
живой поток. Выдавать снимок за живые данные значило бы показывать вчерашнее
состояние как сегодняшнее.
"""
import html
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data" / "floor.html"
d = json.loads((ROOT / "data" / "snapshot.json").read_text(encoding="utf-8"))
e = html.escape


def vclass(v):
    return {"РАБОТАЕТ": "ok", "НЕТ ВХОДА": "wait"}.get(v, "bad")


matrix = "".join(
    '<div class="link {c}"><div class="lk-top"><span class="badge">{v}</span>'
    '<b>{s}</b></div><div class="lk-sub">{o}{b}</div></div>'.format(
        c=vclass(m["verdict"]), v=e(m["verdict"]), s=e(m["stage"]),
        o=e(m["owner"] or ""),
        b=(' · <span class="warn">' + e(m["blocker"]) + "</span>") if m["blocker"] else "")
    for m in d["matrix"])

agents = "".join(
    '<tr><td>{a}</td><td class="num">{r}</td><td class="num {c}">{x}</td></tr>'.format(
        a=e(x["agent"]), r=x["runs"], c=("bad" if x["errors"] else "dim"),
        x=x["errors"] or 0)
    for x in d["agents"])

paths = "".join(
    '<tr><td>{p}</td><td>{c}</td><td class="num">{s}</td></tr>'.format(
        p=e(x["platform"]), c=e(x["category"]), s=int(x["score"] or 0))
    for x in d["open_paths"]) or '<tr><td colspan="3" class="dim">открытых путей нет</td></tr>'

walls = "".join("<li><b>{n}×</b> {w}</li>".format(n=w["n"], w=e(w["wall"]))
                for w in d["walls"])
ok_links = sum(1 for m in d["matrix"] if m["verdict"] == "РАБОТАЕТ")

CSS = """
:root{--bg:#0a0e12;--card:#11171e;--sunk:#0d1319;--line:#1f2a35;--ink:#dde6ee;
--dim:#78889a;--ok:#3fbf8f;--wait:#c9a227;--bad:#e2614c;--acc:#4a9de0}
:root[data-theme="light"]{--bg:#eef1f4;--card:#fff;--sunk:#e4e9ee;--line:#d2dae2;
--ink:#131a21;--dim:#5b6a78;--ok:#0d7d59;--wait:#8a6a12;--bad:#b33a24;--acc:#1f6fb0}
@media(prefers-color-scheme:light){:root:not([data-theme="dark"]){--bg:#eef1f4;--card:#fff;
--sunk:#e4e9ee;--line:#d2dae2;--ink:#131a21;--dim:#5b6a78;--ok:#0d7d59;--wait:#8a6a12;
--bad:#b33a24;--acc:#1f6fb0}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.55 Archivo,system-ui,-apple-system,"Segoe UI",sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:38px 22px 80px}
h1{font-size:30px;font-weight:700;letter-spacing:-.02em;margin:6px 0}
h2{font-size:11px;font-weight:700;letter-spacing:.16em;text-transform:uppercase;
color:var(--dim);margin:38px 0 12px}
.lede{color:var(--dim);max-width:66ch;margin:0;font-size:14px}
.stamp{font-family:"JetBrains Mono",ui-monospace,monospace;font-size:11px;color:var(--dim)}
.num{font-family:"JetBrains Mono",ui-monospace,monospace;font-variant-numeric:tabular-nums}
.mission{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:1px;
background:var(--line);border:1px solid var(--line);margin:26px 0 0}
.m{background:var(--card);padding:16px 18px}
.m b{display:block;font-family:"JetBrains Mono",ui-monospace,monospace;font-size:26px;
font-weight:700;line-height:1.1;font-variant-numeric:tabular-nums}
.m span{display:block;font-size:11px;color:var(--dim);text-transform:uppercase;
letter-spacing:.08em;margin-top:5px}
.m.zero b{color:var(--dim)}.m.good b{color:var(--ok)}.m.alert b{color:var(--bad)}
.chain{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:9px}
.link{background:var(--card);border:1px solid var(--line);border-left-width:3px;
padding:11px 13px}
.link.ok{border-left-color:var(--ok)}.link.wait{border-left-color:var(--wait)}
.link.bad{border-left-color:var(--bad)}
.lk-top{display:flex;gap:8px;align-items:baseline;font-size:13px}
.badge{font-family:"JetBrains Mono",ui-monospace,monospace;font-size:9.5px;
letter-spacing:.06em;padding:2px 6px;background:var(--sunk);color:var(--dim);white-space:nowrap}
.link.ok .badge{color:var(--ok)}.link.wait .badge{color:var(--wait)}
.link.bad .badge{color:var(--bad)}
.lk-sub{font-size:11.5px;color:var(--dim);margin-top:5px}
.warn{color:var(--bad)}
.cols{display:grid;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));gap:26px}
.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:13.5px}
th{text-align:left;font-size:10px;letter-spacing:.12em;text-transform:uppercase;
color:var(--dim);padding:0 12px 7px 0;border-bottom:1px solid var(--line);font-weight:600}
td{padding:8px 12px 8px 0;border-bottom:1px solid var(--line)}
td.num{text-align:right;padding-right:0}
.dim{color:var(--dim)}.bad{color:var(--bad)}
ul{margin:0;padding-left:18px;color:var(--dim);font-size:13.5px}
li{margin-bottom:6px}
.note{background:var(--card);border:1px solid var(--line);border-left:3px solid var(--acc);
padding:15px 17px;font-size:13.5px;color:var(--dim);margin-top:12px}
.note b{color:var(--ink)}
"""

PAGE = """<title>P0 Agent Floor</title>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@500;600;700&family=JetBrains+Mono:wght@400;500;700&display=swap">
<style>{css}</style>
<div class="wrap">
<div class="stamp">СНИМОК СОСТОЯНИЯ · живой дашборд работает локально на 127.0.0.1:8402/dashboard</div>
<h1>P0 &middot; Agent Floor</h1>
<p class="lede">Что система сделала на самом деле. Каждое число взято из базы
в момент сборки страницы, ни одно не оценено приблизительно.</p>

<div class="mission">
  <div class="m {paycls}"><b>{payments}</b><span>платежей</span></div>
  <div class="m good"><b>{spend}</b><span>потрачено</span></div>
  <div class="m"><b>{runs}</b><span>шагов за сутки</span></div>
  <div class="m {errcls}"><b>{errors}</b><span>сбоев за сутки</span></div>
  <div class="m"><b>{oklinks}/{total}</b><span>звеньев работают</span></div>
</div>

<h2>Цепочка от нуля до выручки — проверена вызовом</h2>
<div class="chain">{matrix}</div>
<div class="note">Звено засчитано, только если код есть, его кто-то вызывает,
вызов прошёл, инструмент доступен, гейт пропустил, в базе появилась строка,
результат уходит дальше и работу есть чем подтвердить. <b>«Нет входа»</b>
значит, что звено исправно, но данных туда ещё не приходило: у сервисов лидов
не нашлось дефектов, платежей не поступало. Это не поломка и не успех.</div>

<div class="cols">
<div>
  <h2>Агенты за сутки</h2>
  <div class="scroll"><table>
  <thead><tr><th>агент</th><th class="num">прогонов</th><th class="num">сбоев</th></tr></thead>
  <tbody>{agents}</tbody></table></div>
</div>
<div>
  <h2>Открытые пути к деньгам</h2>
  <div class="scroll"><table>
  <thead><tr><th>площадка</th><th>класс</th><th class="num">оценка</th></tr></thead>
  <tbody>{paths}</tbody></table></div>
  <h2>Что закрывает остальные</h2>
  <ul>{walls}</ul>
</div>
</div>

<h2>Разведка заработка</h2>
<div class="mission">
  <div class="m"><b>{cats}</b><span>классов в обходе</span></div>
  <div class="m {blindcls}"><b>{blind}</b><span>не размечено</span></div>
  <div class="m"><b>{allpaths}</b><span>площадок проверено</span></div>
  <div class="m {opencls}"><b>{openpaths}</b><span>открыто для нас</span></div>
</div>

<h2>Работа наружу и доказательства</h2>
<div class="mission">
  <div class="m"><b>{prs}</b><span>отправлено PR</span></div>
  <div class="m"><b>{claims}</b><span>утверждений сверено с кодом</span></div>
  <div class="m"><b>{evid}</b><span>находок</span></div>
  <div class="m"><b>{srcs}</b><span>источников</span></div>
  <div class="m"><b>{fixes}</b><span>починок кода</span></div>
</div>

<h2>Связь между агентами</h2>
<div class="mission">
  <div class="m"><b>{ask}</b><span>вопросов</span></div>
  <div class="m"><b>{answer}</b><span>ответов</span></div>
  <div class="m"><b>{handoff}</b><span>передач работы</span></div>
  <div class="m"><b>{chat}</b><span>реплик</span></div>
</div>

<div class="note">Миссия засчитывается только пока <b>потрачено = 0</b> и платёж
пришёл от постороннего, а не от владельца. Платежей пока <b>{payments}</b> —
это главный и единственный незакрытый пункт, и он зависит от третьей стороны,
а не от объёма нашей работы.</div>
</div>
"""

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(PAGE.format(
    css=CSS, matrix=matrix, agents=agents, paths=paths, walls=walls,
    payments=d["payments"], paycls=("good" if d["payments"] else "zero"),
    spend=d["spend"], runs=f'{d["runs_24h"]:,}'.replace(",", " "),
    errors=d["errors_24h"], errcls=("alert" if d["errors_24h"] else "good"),
    oklinks=ok_links, total=len(d["matrix"]),
    cats=d["categories"], blind=d["blind"],
    blindcls=("alert" if d["blind"] else "good"),
    allpaths=d["paths"], openpaths=d["paths_open"],
    opencls=("good" if d["paths_open"] else "zero"),
    prs=d["prs"], claims=d["claims"], evid=d["evidence"], srcs=d["sources"],
    fixes=d["fixes"], ask=d["msgs"]["ask"], answer=d["msgs"]["answer"],
    handoff=d["msgs"]["handoff"], chat=d["msgs"]["chat"],
), encoding="utf-8")
print(f"страница собрана: {OUT} ({OUT.stat().st_size:,} байт)")
