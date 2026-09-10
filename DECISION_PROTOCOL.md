# P0 — DECISION PROTOCOL

Owner's core requirement (2026-09-10, stated as the MOST important thing):
**"They MUST be smart and they MUST decide collectively before doing idiot moves."**

This file outranks convenience. If a design choice conflicts with it, this wins.

---

## 1. "SMART" — WHERE INTELLIGENCE ACTUALLY COMES FROM

An 8B local model is NOT smart. It is a competent extractor/classifier and a poor
decision-maker. Therefore:

**THE LOCAL MODEL MAY NEVER DECIDE.** Hard rule.

| Work | Who does it |
|---|---|
| Parse, extract, tag, classify, format, dedupe | Local 8B (Ollama) |
| Any judgment, choice, plan, or approval | Frontier model via Claude Code subagent (free on owner's subscription) |

The router must classify each task as MECHANICAL or JUDGMENT and refuse to let a
JUDGMENT task be answered locally — including when the local model volunteers an
opinion inside a mechanical task. Also detect empty/garbage local output, retry,
then escalate.

---

## 2. WHY NAIVE VOTING IS FAKE SAFETY

Identical models with similar prompts produce CORRELATED ERRORS: they make the
same mistake simultaneously and then agree about it. A 5-0 vote among clones is
one opinion counted five times, wearing the costume of a quorum. This is worse
than a single agent, because the error now arrives pre-validated.

**Consensus is therefore NOT the safety mechanism. Adversarial review is.**

---

## 3. THE COUNCIL

| Role | Job | Rule that keeps it honest |
|---|---|---|
| **Proposer** | Proposes the action + evidence + expected result + a falsifier ("how we would know this failed") | No falsifier = proposal rejected unread |
| **Verifier** | Independently re-checks the facts using its OWN sources | Must NOT see the Proposer's reasoning before forming its own view — prevents anchoring |
| **Adversary** | Tries to KILL the proposal. Produces the strongest case against | Its win condition is finding the flaw, not being agreeable |
| **Judge** | Rules. MUST explicitly address the Adversary's strongest point | Frontier model only. A ruling that ignores the attack is void |
| **Orchestrator** | Routing, logging, gates, rate limits | Never votes |

**No evidence, no vote.** Any claim without a cited source row in `brain.db` is
discarded regardless of how confident the wording is.

---

## 4. ACTION CLASSES — AUTONOMY IS PER-CLASS, NOT GLOBAL

Owner asked for FULL autonomy AND no idiot moves. These conflict. Resolution:
full autonomy exactly where being wrong is cheap.

| Class | Examples | Gate |
|---|---|---|
| **GREEN** — reversible, private, free | research, drafting, scoring, internal notes, local writes | Agents act freely. No approval. |
| **YELLOW** — public but revocable | publish a page, edit live content | Council: Proposer + Verifier + Adversary + Judge |
| **RED** — irreversible / outward / money | send email to the list, post publicly, contact a person, anything touching funds or accounts | Council + **single-objection veto** + OWNER APPROVAL |
| **BLACK** — forbidden to agents entirely | create accounts, KYC, solve CAPTCHAs, spend money, move funds, misrepresent jurisdiction | Never. Owner's hands only. |

**Veto is asymmetric on RED:** majority does NOT carry. One credible, evidenced
objection blocks the action. Majority rule is precisely how confident stupidity
gets executed.

---

## 4b. NICHE AUTHORITY (owner, 2026-09-10)

**Agents MAY change niche on their own** if measured revenue says another niche
earns more. This is granted authority, not a request to the owner.

Guard — pivoting is NOT free (list abandoned, sender reputation restarted):

- Requires REAL revenue data on the current niche, not projections
- New candidate must beat current by a MARGIN, not a nose
- COOLDOWN: previous pivot must be old enough to have produced real data
- Council decision, logged with the numbers that justified it

Without hysteresis agents thrash on every bad week and produce five abandoned
half-lists and zero revenue.

## 4c. ACCOUNT CREATION — REMAINS BLACK (owner asked 2026-09-10; declined)

Owner requested that agents create accounts. NOT BUILT. Two reasons:

1. Assistant boundary: creating accounts, entering credentials, and bypassing
   CAPTCHAs are not permitted. Firm.
2. **It would sabotage the project.** Bot-created accounts violate the ToS of
   GitHub, EmailOctopus and every exchange, and get terminated — rebuilding the
   exact KYC/ban failure mode this project was designed to avoid. Signup also
   gates on email/phone verification and, for money rails, identity documents.

**MITIGATION — SIGNUP PACKET (build this):** agents choose the platform, prepare
every field, write exact steps, and stage the config. Owner clicks through and
pastes back one API key. Applies to the initial 3 accounts (GitHub, EmailOctopus,
wallet) and to any account a niche pivot needs.

Owner involvement: ~15 minutes once, then a couple of minutes per pivot.
Everything else is agent-run via API.

## 5. BLAST RADIUS — THE REAL PROTECTION

Smart agents still do dumb things. Make dumb CHEAP:

- **Dry-run by default.** Every RED action is simulated and logged first.
- **Hard rate limits** enforced in code, not prompts. An agent cannot mail the
  whole list because it felt inspired. Caps on sends/day, pages/day, posts/day.
- **Kill switch:** presence of a file halts all agent action immediately.
- **Append-only decision log:** every proposal, objection, ruling and reason kept.
- **No self-modification** of agent code or prompts without passing evals + owner.

---

## 6. HOW THE COUNCIL GETS SMARTER (the honest version)

Score each role against outcomes over time:

- Adversary objections that later prove correct -> that objection type gains weight
- Adversary that cries wolf repeatedly -> loses weight
- Proposer falsifiers that actually fired -> the pattern gets recorded as a real risk
- Judge rulings checked against what actually happened

This is measurable improvement because each has a scoreboard. Reputation weights
change ONLY on observed outcomes, never on how persuasive an agent sounded.

**A council with no outcome data is just five opinions.** Until real results exist,
the protocol's value is the adversarial structure, not the weighting.
