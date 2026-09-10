# P0 — PLAN

Version: 0.1 (2026-09-10). Canon = DIRECTIVE.md. Read that first.

---

## SCOPE CHANGE — 2026-09-10 (owner: "pick something simpler")

Owner is a **RUSSIAN RESIDENT** (stated 2026-09-10, mentioned using a VPN).

**VPN DOES NOT SOLVE THIS.** A VPN hides browsing; it does not survive KYC.
Programs verify identity by documents and banking AT WITHDRAWAL. A mismatch
between declared and verified jurisdiction freezes the account at the exact
moment money is claimed, with the earnings inside. For a project whose purpose
is collecting money, this is the primary failure mode, not a footnote.
=> Russian-resident acceptance becomes a HARD GATE verified per offer.
=> Agents must never be instructed to misrepresent jurisdiction.

**BINANCE IS OUT** (verified 2026-09-10): sold its entire Russian business to
CommEX in Sept 2023, no longer operates in Russia in any form; restricts Russian
users under EU sanctions. EU's 21st sanctions package (23 July 2026) barred
operators from transacting with 14 Russia-linked crypto platforms. The
no-minimum real-time payout cited earlier today is NOT available to this owner.
Lesson: verify the gate per offer. Do not trust general "best of" listings.

### RAIL FIRST, BUSINESS SECOND

The binding constraint is not the AI and not the niche — it is the payout rail.
Simplifying the work does not simplify collection. Therefore P0 solves the rail
before choosing the business.

**Simplest surviving rail: direct payment to a self-custody wallet.** No exchange,
no KYC, no geo-block, no intermediary with the power to ban. Every intermediary
added (exchange, CPA network, payment processor) reintroduces the KYC/sanctions
problem that just eliminated Binance.

**Two candidate models, agents to decide on evidence:**

| Model | Rail | Weak point |
|---|---|---|
| A. Digital product sold direct for crypto | Wallet, no KYC, no geo-block | Needs audience trust |
| B. Content + crypto-paying affiliate | Affiliate program | Rail depends on RU acceptance — just failed once |

A has the stronger rail; B has easier scale.

### AGENT CAPABILITY BOUNDARY (hard)

Agents CANNOT create accounts, pass KYC, or solve CAPTCHAs. Owner's hands only.
Agents operate inside accounts that already exist. Any plan implying otherwise
is false.

### SIMPLIFIED ROSTER

Five agents was too much for the first build. **P0 ships ONE agent** (Scout, with
the Adversary pass as a second prompt rather than a second service). The roster
below is the P1 target, not the P0 deliverable.

### OWNER'S OWN OBLIGATION (not agent work)

Russian tax residency: income must be declared by the owner. Out of scope for the
agents, flagged so it is not forgotten.

---

## WHAT P0 DELIVERS

NOT the money machine. P0 delivers **the decision, backed by evidence** — which
niche, which offers, which free traffic channel — produced by agents, plus the
data spine and agent skeleton that P1 reuses to actually build the list.

Success condition for P0: a ranked shortlist where every claim links to a source
row in the database, the top candidate survives an adversarial pass, and the
recommended offers are verified to pay in BTC/ETH/USDT.

---

## ARCHITECTURE

**The spine is local and owned.** `brain.db` (SQLite) holds niches, offers,
evidence, sources, scores, and later subscribers. Every external platform is a
replaceable pipe. Rationale: senders in restricted niches get suspended and lose
their lists — a ban must cost us a pipe, never the asset.

    agents/          role prompts + code, one file per agent
    core/            db, llm router, search/fetch, logging
    data/brain.db    the spine
    reports/         agent output for the owner
    evals/           held-out set + baseline. Nothing promotes without beating it.

**Model routing (zero budget):** local 8B via Ollama on the RTX 5070 does bulk
extraction, classification and tagging. Judgment calls escalate to a Claude Code
subagent — free on the owner's existing subscription, no API key, no bill.
Known risk from prior experience: small local models return empty responses under
load. The router must detect empty/garbage output, retry, then escalate.

---

## AGENT ROSTER FOR P0

| # | Agent | Job | How it can be wrong (its scoreboard) |
|---|---|---|---|
| 1 | Scout | Search + fetch + extract candidate niches and offers into evidence rows, each with a source URL and retrieval date | Claims with no source row are rejected automatically |
| 2 | Verifier | Independently re-checks the hard gates on the shortlist, especially payout rail and signup openness | Disagreement with Scout is logged, not silently merged |
| 3 | Scorer | Applies the rubric below; must cite evidence row IDs for every score | A score citing no evidence is invalid |
| 4 | Adversary | Actively tries to KILL each top candidate — find the reason it fails | A candidate that survives a real attack is worth more than one nobody attacked |
| 5 | Orchestrator | Queue, routing, retries, memory, report assembly | Run completes without silent failures |

Five agents. Not twenty. Each has a job whose output can be checked.

---

## SCORING RUBRIC

**Hard gates — fail any one and the candidate is eliminated, no score:**

- **G1. Payout in BTC/ETH/USDT.** Owner requirement. Wire/PayPal-only = dead.
- **G2. Signup open to a no-name applicant** with zero traffic history and no
  company. Many high-payout programs reject unknown applicants — must be checked,
  not assumed.
- **G3. Permitted by the ESP** under an opt-in model (EmailOctopus AUP:
  legitimate industry + consent; affiliate marketing gets extra scrutiny).
- **G4. A free traffic channel exists** with a reachable audience and no ad spend.
- **G5. Zero purchase required** to start.
- **G6. Rule Zero clean** — no overlap with any prior project or account.

**Scored 1-5 only after all gates pass:**

- S1. Commission size, and recurring vs one-time
- S2. Audience reachability at zero budget
- S3. Competition density
- S4. Content cost — can a local 8B model produce credible material here?
- S5. Platform/ban risk

---

## PHASES

- **P0.1** Spine: repo skeleton, `brain.db` schema, Ollama daemon up, model pulled,
  LLM router with empty-response detection + escalation. Smoke test.
- **P0.2** Scout agent. Output: evidence rows with sources. Checked by hand once.
- **P0.3** Verifier + Scorer. Output: gated, scored shortlist.
- **P0.4** Adversary pass on the top 3.
- **P0.5** Decision report to owner: niche, 3 offers, traffic channel, and the
  cheapest real-world test that would prove or kill it.

**P1 (only after owner reads P0.5):** the actual list machine — landing page,
opt-in, sequence, tracking, and the eval harness that makes "learning" real.

---

## WHAT ACTUALLY SETTLES THE TRUTH

No amount of agent research proves a niche works. Research produces a *ranked
hypothesis*. Ground truth arrives only when real traffic meets a real opt-in form
and real people either subscribe and click, or don't. P0 ends by naming the
cheapest test that produces that signal; P1 runs it.

This is also the point where "auto-learning" becomes honest rather than
decorative: once there is a conversion number, the agents have something that can
tell them they were wrong.

---

## KNOWN OPEN RISKS

- Free sending domain = weak deliverability, and Gmail/Yahoo/Microsoft now BLOCK
  senders without SPF+DKIM+DMARC (Gmail escalated from warning to blocking in late
  2025). A free subdomain may not permit DNS control. Unsolved at zero budget.
- 24/7 operation: the laptop sleeps. Free always-on hosting unverified.
- Owner jurisdiction affects exchange KYC and which programs accept the signup.
  UNANSWERED — flagged to owner.

---

## VERIFIED API SURFACE — EmailOctopus, probed 2026-09-10

| Endpoint | Status |
|---|---|
| `/lists`, `/lists/{id}/contacts` | 200 — works |
| `/account` | 200 — name, plan, limits, created_at |
| `/campaigns` | 200 — works |
| `/landing-pages`, `/forms`, `/tags`, `/reports`, `/automations` | **404 — do not exist** |

**Consequences:**

1. **Profile image / account branding is NOT API-reachable.** Owner granted full
   permission (2026-09-10) but the API does not expose it — dashboard only.
   Nothing for agents to call. Not a permissions issue.
2. **Landing pages and forms are NOT API-reachable either.** Therefore opt-in
   pages are hosted on **GitHub Pages** and subscribers created via the contacts
   API. Agents control this end to end (git push + HTTP), no dashboard, no
   CAPTCHA. This is BETTER for autonomy than the free plan's built-in pages.
3. **Cloudflare error 1010** blocks the default `Python-urllib` User-Agent.
   Any explicit UA works. Client sets `P0-agent/0.1`. Verified 2026-09-10.

## LEAD QUALITY POLICY (owner asked for "rich leads", 2026-09-10)

Instinct is right; the mechanism must change.

**Agents CANNOT select subscribers.** Opt-in means people come to us. There is no
step where an agent picks who joins. And identifying wealthy individuals directly
would require buying data (breaks the $0 claim), scraping personal data (breaks
GDPR / 152-FZ and the ESP's own terms), or profiling. Each costs either money or
the account.

**POLICY: target rich CONTEXTS, not rich people.** Pick a topic whose audience
inherently has budget and already buys online (B2B operators, developers, agency
owners, traders); publish where they gather; let the content self-select. Legal,
free, and effective.

**Tension logged:** high-value audiences are hardest to win with a no-name free
page, and the MISSION is fastest verified payment, not best audience. When these
conflict, MISSION WINS — proof first, audience quality second.

## SENDING ENVELOPE (owner granted full authority over the EO account)

Agents have full authority on the EmailOctopus account. Sending to humans is
bounded not by approval but by a **code-enforced cap** (`SEND_CAP_PER_DAY` in
core/eo.py). Agents act freely below it and cannot exceed it. Rationale: one bad
send on a fresh affiliate-scrutinized account terminates the only sending pipe,
and the mission dies with it.

---

# ============ STRATEGY REVISION 2026-09-14 — FASTEST PROOF PATH ============

## The finding that reorders everything

**x402 Bazaar** (docs.x402.org/extensions/bazaar) is a free, machine-readable
discovery layer where AI agents FIND and PAY FOR endpoints autonomously.

- Listing is FREE. Seller sets their own price per call.
- Seller opts in via a metadata blob on the V2 settle call; **indexed within ~30
  seconds of the first confirmed settle**.
- Agents query Bazaar for matching capability and pay in milliseconds.
- **No audience, no traffic, no trust-building, no list required.**

## Consequence: EMAIL IS NOT ON THE CRITICAL PATH TO THE PROOF

| Path | What it needs | Time to first payment |
|---|---|---|
| Email list -> affiliate | audience -> trust -> offer -> conversion | weeks to months |
| **x402 endpoint -> Bazaar** | **a working API + a wallet** | **days** |

MISSION = fastest verified payment. Therefore **x402 endpoint is PHASE 1**.
EmailOctopus becomes PHASE 2 (scale), not phase 1 (proof). The account is not
wasted; it is simply not the bottleneck.

## Verified evidence supporting this (in brain.db)

- ev#2: x402 protocol is PERMISSIONLESS — anyone may run a facilitator; payloads
  are buyer-signed and settled directly onchain. KYC/KYT applies to PRIVATE
  ENTERPRISE facilitators, not to the protocol. Self-custody receiving needs no
  account. (source: docs.x402.org/faq)
- ev#3: MEASURED — qwen3.5:9b was handed the passage containing the answer and
  replied "NOT FOUND" (47s); second page timed out at 120s. The local model is
  unfit for judgment. DECISION_PROTOCOL rule 1 is now empirical, not assumed.

## REMAINING BLOCKERS TO FIRST PAYMENT

1. **Facilitator usable from RU without KYC.** OPEN — standing objection on
   proposal #1. CDP (Coinbase) facilitator likely requires an account; community
   facilitators exist. MUST be resolved before building.
2. **Wallet address** — owner has one; not yet in .env.
3. **An endpoint worth paying for.** The real product question. Must be something
   an agent genuinely needs and cannot trivially do itself.
4. **Bootstrap listing** — Bazaar indexes after the FIRST confirmed settle, so
   there is a chicken-and-egg step to solve.

## NEXT COUNCIL QUESTION

"What endpoint can we build, for free, that an AI agent would genuinely pay for?"
This is a JUDGMENT call -> frontier escalation, never the local model.
