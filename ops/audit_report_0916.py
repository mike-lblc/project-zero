"""Аудит экосистемы и агентов P0 — 16.09.2026. Страница собирается из измеренных данных.

    py -3.13 -X utf8 ops/audit_report_0916.py <audit_agents.json> docs/AUDIT_2026-09-16.html
"""
from __future__ import annotations

import html
import json
import sys
from pathlib import Path

VERDICT_CLASS = {"ДЕЙСТВУЕТ": "act", "ИЩЕТ": "seek", "ДЕЖУРИТ": "watch", "ВХОЛОСТУЮ": "idle", "МОЛЧИТ": "silent",
                 "ТОЛЬКО СУЩЕСТВУЕТ": "silent"}

RECO = {
    "salesman": ("Only agent reaching buyers: 20 GitHub messages, 4 replies, 0 sales. Messages carried no price until today.",
                 "Keep. Judge the priced pitch on the next 50 sends by reply rate; the verified-channel pool is 263 and thinning."),
    "dealer": ("233 of 235 attempts were hand-backs to other agents; 2 Moltbook asks got 0 replies. Cannot reach the 61 known buyer wallets.",
               "Merge its GitHub side into salesman; keep only Moltbook buyer posts. Retire the 72-hour manual 1-cent ask."),
    "closer": ("Reported a dead thread as a live negotiation for 3 days. Since today it replies to leads itself and closes dead threads (3 closed).",
               "Keep. Watch the first autonomous replies for tone."),
    "leads": ("949 leads, 278 with a verified channel; new channels found: 0 since 15.09. 4 of 19 contacted channels were not the seller's.",
              "Keep at lower cadence. The ownership gate was tightened today."),
    "channel_manager": ("223 runs in 7 days, repeating demand scans and channel checks; nothing reached outside.",
                        "Merge into leads (channel checks) and dealer (Moltbook demand)."),
    "collector": ("Watches 33 payment routes on 8 networks; 810 runs, 0 receipts. Correctly records nothing.",
                  "Keep as a timed step; it needs no reasoning turns."),
    "merchant": ("Priced against the whole market instead of our niche; top tiers sat where the niche has almost no payers.",
                 "Repriced today. Keep as a rare step; recompute from niche data only."),
    "bounty": ("1,187 runs and 189 errors in 7 days (devpost outage 13.09, old import bug 11.09). Repeats the same bounty scan every few minutes.",
               "Scan less often; spend its turns on Taskmarket and AIBTC intake, which it owns."),
    "craftsman": ("1 merged PR (omi quickstart), bounty unpaid. Finds 0 doable GitHub bounties; now owns Taskmarket work.",
                  "Keep. Its value now depends on Taskmarket tasks of our class."),
    "executor": ("89 runs, 4 findings, 0 deliveries. Overlaps craftsman.", "Merge into craftsman."),
    "browser_scout": ("11 runs in 7 days; off-GitHub task scan only.", "Keep as a rare step."),
    "prospector": ("Probe was stuck on the same 6 platforms since 11.09; 865 of 911 never checked; its 9 'open' paths were false positives.",
                   "Rotation fixed today (all 911 now checked once). Re-read results before trusting 'open'."),
    "explorer": ("103 findings and 87 answers to other agents in 7 days — the most useful thinker.", "Keep."),
    "scout": ("1,757 runs refreshing the market catalog (16,000+ services); 211 findings.", "Keep; halve the light-probe cadence."),
    "supplier": ("Repeats 'live integrations 11 of 11'; 2 findings.", "Merge into watchdog."),
    "orchestrator": ("Loop plumbing, reasoning slot, escalation watch; 909 runs.", "Keep."),
    "watchdog": ("1,946 runs; lists silent agents and now asks them; 0 findings of its own.", "Keep; lower the list cadence."),
    "critic": ("114 runs, 3 findings; overlaps adversary.", "Merge into adversary."),
    "adversary": ("Runs the MTBX audit (54/54 now); 19 findings.", "Keep."),
    "verifier": ("Re-checks evidence rows; 117 runs.", "Keep."),
    "optimizer": ("Time-spend accounting; 64 findings.", "Keep as a rare step."),
    "improver": ("19 code fixes accepted, 0 reverted since its safeguards.", "Keep with the throttle."),
    "mechanic": ("Spent 112 rounds fixing nothing until 15.09; now 7 fixable items queued. Silent 24 h.", "Keep as a rare step."),
    "scribe": ("Writes a report nobody reads; 4 findings; silent 24 h.", "Retire or fold into optimizer."),
}


def e(x):
    return html.escape(str(x if x is not None else ""))


def main(src, out):
    agents = json.loads(Path(src).read_text(encoding="utf-8"))
    dec = sum(a["dec7"] for a in agents); denied = sum(a["dec_denied"] for a in agents)
    rows = []
    for a in agents:
        cls = VERDICT_CLASS.get(a["verdict"], "watch")
        what, rec = RECO.get(a["agent"], ("—", "—"))
        rows.append(
            f'<tr><td class="name">{e(a["agent"])}<span class="kind">{e(a["kind"])}</span></td>'
            f'<td><span class="chip v-{cls}"><span class="dot"></span>{e(a["verdict"])}</span></td>'
            f'<td class="num">{a["runs_24h"]}<span class="sub">7d {a["runs_7d"]:,}</span></td>'
            f'<td class="num">{a["findings_7d"]}</td><td class="num strong">{a["external_7d"]}</td>'
            f'<td class="num">{a["dec7"]}<span class="sub">denied {a["dec_denied"]}</span></td>'
            f'<td class="num">{a["err7"]}</td>'
            f'<td class="txt">{e(what)}</td><td class="txt">{e(rec)}</td></tr>')

    page = f"""<title>P0 Audit 16 September</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Condensed:wght@500;600&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
:root{{--ground:#EEF1F4;--surface:#FFFFFF;--ink:#16202A;--muted:#5B6875;--line:#D5DBE2;--accent:#B4531B;
--act:#1F7A4D;--seek:#2B5FB0;--watch:#6A737D;--idle:#B7791F;--silent:#B23A3A;--tint:#F6F8FA}}
@media (prefers-color-scheme: dark){{:root:not([data-theme="light"]){{--ground:#0F141A;--surface:#171E27;--ink:#E6EBF0;--muted:#9AA7B4;--line:#2A3440;--accent:#E0763A;
--act:#4CC38A;--seek:#7AA7F0;--watch:#98A2AD;--idle:#E0B15A;--silent:#F08A8A;--tint:#1C2530}}}}
:root[data-theme="dark"]{{--ground:#0F141A;--surface:#171E27;--ink:#E6EBF0;--muted:#9AA7B4;--line:#2A3440;--accent:#E0763A;
--act:#4CC38A;--seek:#7AA7F0;--watch:#98A2AD;--idle:#E0B15A;--silent:#F08A8A;--tint:#1C2530}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--ground);color:var(--ink);font:15px/1.55 "IBM Plex Sans",system-ui,sans-serif;padding-block:32px 56px;padding-inline:20px}}
.wrap{{max-width:1160px;margin:0 auto;display:grid;gap:30px}}
h1,h2{{font-family:"IBM Plex Sans Condensed","IBM Plex Sans",sans-serif;font-weight:600;letter-spacing:-.01em;text-wrap:balance;margin:0}}
h1{{font-size:38px;line-height:1.05}} h2{{font-size:22px;margin-bottom:12px}}
.eyebrow{{font:500 12px/1 "IBM Plex Mono",monospace;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin-bottom:10px}}
header{{border-bottom:3px solid var(--accent);padding-bottom:20px;display:grid;gap:12px}}
header p{{max-width:72ch;margin:0;font-size:17px}}
.tablewrap{{overflow-x:auto;background:var(--surface);border:1px solid var(--line)}}
table{{border-collapse:collapse;width:100%;font-size:14px}}
table.wide{{min-width:1080px}} table.mid{{min-width:720px}}
th{{background:var(--tint);text-align:left;font:500 11px/1.3 "IBM Plex Mono",monospace;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);padding:10px 12px;border-bottom:1px solid var(--line)}}
td{{padding:9px 12px;border-bottom:1px solid var(--line);vertical-align:top}}
tr:last-child td{{border-bottom:0}}
td.num{{font-family:"IBM Plex Mono",monospace;font-variant-numeric:tabular-nums;text-align:right;white-space:nowrap}}
td.strong{{font-weight:500;color:var(--accent)}}
td.txt{{max-width:320px;color:var(--ink)}}
.sub{{display:block;color:var(--muted);font-size:11px;font-family:"IBM Plex Sans",sans-serif}}
td.name{{font-weight:600;white-space:nowrap}} .kind{{display:block;color:var(--muted);font-weight:400;font-size:11px}}
.dot{{width:10px;height:10px;border-radius:50%;display:inline-block;background:currentColor;flex:none}}
.chip{{display:inline-flex;align-items:center;gap:6px;font-weight:600;font-size:13px;white-space:nowrap}}
.chip.v-act{{color:var(--act)}} .chip.v-seek{{color:var(--seek)}} .chip.v-watch{{color:var(--watch)}} .chip.v-idle{{color:var(--idle)}} .chip.v-silent{{color:var(--silent)}}
.score{{font:500 22px/1 "IBM Plex Mono",monospace;font-variant-numeric:tabular-nums}}
.bar{{height:6px;background:var(--line);margin-top:6px;max-width:140px}} .bar i{{display:block;height:6px;background:var(--accent)}}
.cols{{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:24px}}
.panel{{background:var(--surface);border:1px solid var(--line);padding:18px 20px}}
ul,ol{{margin:0;padding-left:20px;display:grid;gap:8px}} li b{{font-weight:600}}
.note{{color:var(--muted);font-size:13px;max-width:90ch}}
:focus-visible{{outline:2px solid var(--accent);outline-offset:2px}}
@media (max-width:520px){{h1{{font-size:30px}} body{{padding-inline:16px}}}}
</style>
<div class="wrap">
<header>
  <div class="eyebrow">P0 · ecosystem and agents audit · 16 September 2026 · measured 17:00–18:30 UTC</div>
  <h1>P0 Audit 16 September</h1>
  <p>No payment has arrived, and until today no standard x402 client could have paid us: the payment requirements lacked the USDC signing domain. That is fixed and live, along with a transfer-based purchase path, silent handoffs, dead deals counted as live, and an ownership check that let messages reach the wrong repositories. Agents are busy; almost none of the effort reaches a buyer.</p>
</header>

<section>
  <h2>Scorecard</h2>
  <div class="tablewrap"><table class="mid">
  <thead><tr><th>Area</th><th>Score</th><th>Why</th></tr></thead><tbody>
  <tr><td class="name">Payment path</td><td><span class="score">7</span><div class="bar"><i style="width:70%"></i></div></td><td>Was 2 this morning: the 402 lacked <code>extra</code> {{name, version}}, so reference clients throw before signing. Fixed and verified live, plus <code>/pay</code> and <code>?tx=</code> for plain transfers and <code>/stats.json</code> counters. Unproven until a first settlement.</td></tr>
  <tr><td class="name">Rails &amp; infrastructure</td><td><span class="score">6</span><div class="bar"><i style="width:60%"></i></div></td><td>Zero step errors after each restart, all audits green. Two long outages in 24 h: the owner's stop from 17:30 to 11:09 UTC, and a machine reboot at 12:25 UTC with work resuming at 16:38. KV writes were at 880 of a 1,000 daily cap (fixed).</td></tr>
  <tr><td class="name">Code &amp; rules</td><td><span class="score">7</span><div class="bar"><i style="width:70%"></i></div></td><td>177 tests pass; 122 invariants; audits 91/0, 6/0, MTBX 54/0; every network and subprocess call has a timeout. Ollama was cutting agent prompts to 2,050 tokens (fixed: 8,192).</td></tr>
  <tr><td class="name">Security</td><td><span class="score">8</span><div class="bar"><i style="width:80%"></i></div></td><td>No secrets in tracked files; keys and recovery code only in <code>.env</code> and the local keystore; public board free of secrets and e-mails; board push refuses requests without the token (403); 202 injection probes rejected.</td></tr>
  <tr><td class="name">Work &amp; discovery agents</td><td><span class="score">4</span><div class="bar"><i style="width:40%"></i></div></td><td>3 Taskmarket submissions, still unsettled; 0 AIBTC deliveries; 0 doable GitHub bounties; platform probe stuck for 5 days (fixed today).</td></tr>
  <tr><td class="name">Oversight &amp; collaboration</td><td><span class="score">5</span><div class="bar"><i style="width:50%"></i></div></td><td>Audits run themselves, but 27 handoffs in a week were never taken (fixed today) and about {round(100 * denied / max(dec, 1))}% of reasoning decisions were denied as out-of-menu ({denied} of {dec}).</td></tr>
  <tr><td class="name">Sales funnel agents</td><td><span class="score">3</span><div class="bar"><i style="width:30%"></i></div></td><td>20 messages, 4 replies, 0 sales. The only "negotiation" had been closed by the maintainer three days earlier. Messages had no price until today.</td></tr>
  </tbody></table></div>
</section>

<section>
  <h2>What was claimed, and what the checks show</h2>
  <div class="tablewrap"><table class="mid">
  <thead><tr><th>Claim from earlier work</th><th>Status</th><th>Evidence</th></tr></thead><tbody>
  <tr><td>Shared board, live chat, public board</td><td><span class="chip v-act"><span class="dot"></span>done</span></td><td>/board 200, board.json 82 KB fresh after restart; dashboard tab "Живой чат" on Pages; push without token → 403.</td></tr>
  <tr><td>Payments in any asset with an address</td><td><span class="chip v-act"><span class="dot"></span>done</span></td><td>33 routes verified on 8 networks incl. TRON; rule 7 in every agent's rules; history before 12.09 ignored.</td></tr>
  <tr><td>Taskmarket runs without a session</td><td><span class="chip v-idle"><span class="dot"></span>fixed today</span></td><td>Sync had read 20 stale tasks and a wrong balance key; now CLI listing, 10-minute timer (runs 11:09, 11:22, 11:47…), balance read from <code>balanceUsdc</code>.</td></tr>
  <tr><td>Five education-site entries (19 USDC)</td><td><span class="chip v-silent"><span class="dot"></span>not done</span></td><td>The build workflow failed on API authorization (403); the tasks closed at 02:50 UTC. No entry was submitted.</td></tr>
  <tr><td>Agents answer each other; handoffs</td><td><span class="chip v-idle"><span class="dot"></span>fixed today</span></td><td>Answers worked (explorer 87 in a week); handoffs did not (27 made, 0 taken). Owners now execute tool handoffs; 25 stuck ones drained.</td></tr>
  <tr><td>Leads replied to without waiting</td><td><span class="chip v-idle"><span class="dot"></span>fixed today</span></td><td><code>answer_replies</code> ran live: apify thread closed, omi bot comment ignored; deals #60 and #61 closed as dead.</td></tr>
  <tr><td>Gap analysis "what is missing"</td><td><span class="chip v-idle"><span class="dot"></span>partial</span></td><td>All four read-only readers finished with evidence; the three strategists and the synthesizer failed on usage limits. Synthesis below is written from the readers' evidence.</td></tr>
  </tbody></table></div>
</section>

<div class="cols">
  <section class="panel">
    <h2>Resources: public board and dashboards</h2>
    <ul>
      <li><b>Cloudflare worker (API + /board).</b> 1,630–4,830 requests a day of 100,000 free (under 5%). CPU p50 0.6–0.9 ms; p99 was 22–43 ms against the 10 ms free limit with 0 errors — search text is now built once at load.</li>
      <li><b>KV store.</b> Was 480 board writes plus up to 400 counter writes a day against 1,000 free. Now at most 360 (only on change, every 4 min) plus 300. Each open board viewer costs 2,880 reads a day of 100,000 (was 17,280 at 5-second polling). Storage 82 KB of 1 GB.</li>
      <li><b>GitHub Pages dashboard.</b> 52 KB page plus a 160 KB data snapshot; repository 7.2 MB. The cloud replica ran 290 times in 7 days (~161 minutes), free for a public repository.</li>
      <li><b>Local dashboard (port 8402).</b> One Node process, 81 MB RAM, about 1 CPU-second an hour; reads the database every 5 s only while a page is open.</li>
      <li><b>Local worker.</b> 66–286 MB RAM; database 11 MB; logs 3 MB.</li>
      <li><b>The real cost is the local model:</b> 13.4 GB RAM for <code>qwen3-coder:30b</code> plus the 8 GB GPU, 26–37 s per reasoning call.</li>
      <li><b>Not P0:</b> 60 of 61 Node processes are developer tool servers of other desktop applications, 4.4 GB RAM. Free RAM is 2.5 GB of 31.7 GB.</li>
    </ul>
  </section>
  <section class="panel">
    <h2>What is still missing for a first payment</h2>
    <ol>
      <li><b>A buyer who tries to pay.</b> 0 settlements ever; Coinbase Bazaar and PayAI list a seller only after one. <code>/stats.json</code> now counts outside 402s so demand is visible.</li>
      <li><b>An offer people want at the right price.</b> Our niche pays $0.002–$0.01; three tiers sat at $0.10–$1.25. Repriced to $0.01/0.02/0.05/0.25; free outputs still cover small needs.</li>
      <li><b>Fastest proven payers are not worked yet.</b> Taskmarket requester "Kai": $0.01–$0.15 tasks, first qualifying answer wins, 2–20 rivals, 14 different winners — intake now admits them. AIBTC Clarity audits: 32,000 sats open, 4–9 rivals, repeat payer — excluded by our class rule and no agent produces them.</li>
      <li><b>Reach to wallets that already pay.</b> 61 x402 buyer wallets are known; no channel reaches them.</li>
      <li><b>Pitch volume on a thin pool.</b> 263 verified channels left, 2 with 100+ payers; about 15 new a day.</li>
      <li><b>Uptime.</b> 21.8 hours of the last 30 had no agent running (owner stop, then a reboot).</li>
    </ol>
  </section>
</div>

<section>
  <h2>Every agent</h2>
  <div class="tablewrap"><table class="wide">
  <thead><tr><th>Agent</th><th>Verdict (24 h)</th><th>Runs 24 h</th><th>Findings 7 d</th><th>Outside 7 d</th><th>Decisions 7 d</th><th>Errors 7 d</th><th>What it actually did</th><th>Recommendation</th></tr></thead>
  <tbody>{''.join(rows)}</tbody></table></div>
  <p class="note">Counted from the database (runs, evidence, agent_decisions, messages, outreach, dealer_attempts, moltbook_receipts, code_fixes, pull_requests). "Outside" means a trace beyond the ecosystem: a message, post, pull request or code fix. The 24-hour window includes the owner's stop and the reboot, so mechanical steps look quieter than they are. Recommendations are proposals; nothing was merged or retired.</p>
</section>

<section class="panel">
  <h2>Fixed and deployed during this audit</h2>
  <ul>
    <li>USDC signing domain in every x402 requirement; <code>/pay</code>, <code>?tx=</code> transfer verification, <code>/stats.json</code>; tiers repriced; false claims removed from public pages.</li>
    <li>Taskmarket: CLI task listing, balance keys, award evidence before withdrawal, package briefs recorded instead of single-file submissions, 10-minute background sync.</li>
    <li>Funnel: ownership only by homepage or owner login; anomalous numbers not sent; priced pitch; autonomous lead replies; silent closures close deals; handled replies no longer listed as waiting.</li>
    <li>Collaboration: handoffs executed and taken, duplicates collapsed, stale ones expired; local model context 8,192 tokens.</li>
    <li>Resources: board writes only on change, counters capped, board polling 30 s, search without per-request string building.</li>
  </ul>
</section>
</div>
"""
    Path(out).write_text(page, encoding="utf-8")
    print("written", out, len(agents), "agents")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
