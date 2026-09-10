# P0 — DIRECTIVE (canon)

Created: 2026-09-10
Location: C:\Users\chebo\Desktop\Brain
Status: Phase 0 — discovery / feasibility. NOTHING is being built yet.

---

## RULE ZERO — CLEAN SLATE (owner's explicit order, 2026-09-10)

**P0 MUST NOT be connected to ANY previous project, asset, account, dataset or
codebase.** Owner aborted an earlier proposal specifically because it reused
existing business assets.

Explicitly OFF-LIMITS as inputs, data sources, infrastructure or precedent:
- listbuilding.com / LBL Institute / LBL Club / Igor Kheifets properties
- e-Farming, Digistore24 accounts, mailboxlandlord, zeroto.online
- Kit/ConvertKit, beehiiv, or any existing list, subscriber or sending domain
- Bybit grid bot, adaptive_bybit_bot, flip_video_analysis, any trading capital
- EARN5K, Ad Radar, MOBGAME, TTG, OTK Audio Monitor
- The FTP server mirror and its 42 domains

New everything: new domain, new accounts, new audience, new code.

---

## ============ MISSION (owner, 2026-09-10) ============

**THE AGENTS' LIVE PURPOSE: PROVE THEY CAN MAKE MONEY FROM $0, FOR FREE.**

This outranks every other goal in this folder. Not "build a business" — PROVE
A CLAIM. Everything else is subordinate.

### The claim, stated so it can fail

> Autonomous agents, starting from $0, spending $0, can generate verifiable
> revenue.

**PASS:** >=1 row in `payments` with a real on-chain tx hash, traceable to work
the agents did, while `spend` stays empty.

**FAIL (must be honestly declared):** after the agreed window, `spend` is still
empty and `payments` is still empty. The hypothesis loses. We report that.

### What we optimize for

**TIME-TO-FIRST-VERIFIED-PAYMENT.** Not market size, not scale, not long-term
potential. The smallest thing that produces ONE real payment wins.

Consequence: micropayments are an ADVANTAGE. A $0.30 x402 payment proves the
claim exactly as well as $300 and arrives far sooner. Earlier reasoning that
dismissed micropayment economics was optimizing for the wrong target.

### The proof must not be able to cheat

A proof needs an honest ledger of human help, or it is marketing.

- `human_interventions` — EVERY owner touch: account creation, key pastes,
  approvals, rescues. With `agent_could_have` flagged.
- `spend` — must stay EMPTY. Any row invalidates the "$0" claim.
- Final report shows BOTH columns: what agents did, what the owner did.

Already logged as owner work (2026-09-10): created GitHub account, created
EmailOctopus account + 'Audience' list, provided wallet, pasted API key.

---

## THE BUSINESS

Autonomous agent system that:
1. Builds an opt-in email audience from zero
2. Monetizes it through affiliate / CPA offers
3. Learns and improves from measured results

"Email marketing" is the skill. This is a media/audience business — NOT trading,
NOT capital at risk.

**NICHE = AGENT-DECIDED (owner, 2026-09-10).** Crypto was the starting idea but
is no longer locked. The agents research candidate niches, gather evidence, score
them against a rubric and recommend. Crypto stays in the candidate pool — it was
NOT ruled out (see findings below), it is simply no longer assumed.

---

## LOCKED DECISIONS (owner, 2026-09-10)

| Decision | Value |
|---|---|
| Monetization | Opt-in email list -> affiliate/CPA offers |
| Niche | **Agents research and decide** (crypto = one candidate, not locked) |
| Budget | **TRULY ZERO** — free tier or local only, no purchases |
| Autonomy | **FULL** — agents act without approval, except spending money |
| Payout rail | **CRYPTO — BTC / ETH preferred** (owner, 2026-09-10). HARD GATE. |
| Hardware | i9-14900HX, 31.7 GB RAM, RTX 5070 Laptop 8 GB VRAM, 471 GB free |
| Stack available | Python 3.13.12, Node 24.13.1, Ollama (daemon not yet up) |

---

## PAYOUT REQUIREMENT (owner, 2026-09-10) — HARD GATE

Revenue must be withdrawable **in crypto, BTC/ETH preferred**. An offer that pays
only by wire/PayPal is disqualified regardless of how good the niche is. Verified
2026-09-10 that crypto-native rails exist and are strong:

- Binance affiliate: 20-50% of trading fees, real-time USDT/BTC/BNB, NO minimum
- Nexo: up to $100/referred depositor, BTC/ETH/USDT/USDC, $25 minimum
- Bitunix: up to 50% recurring, daily USDT settlement
- Trezor: 12-15%/sale, BTC, $100 minimum

Consequence: the payout requirement and the crypto niche reinforce each other.
This raises crypto's standing in the candidate pool rather than lowering it.

**RULE-ZERO COLLISION:** Bybit's affiliate program qualifies on payout but the
owner has a pre-existing Bybit account. EXCLUDED under Rule Zero. Agents must not
propose it.

Money flow to design in P1: offer -> affiliate dashboard -> owner's wallet.
Agents never hold, route or touch funds. Withdrawal is the owner's hands only.

## HARD LIMITS (non-negotiable, stated to owner 2026-09-10)

1. **Opt-in only.** Autonomy applies to research, writing, publishing to own
   properties, and mailing people who subscribed. NO scraping + cold blasting:
   illegal (CAN-SPAM / GDPR) and self-defeating (new domain blacklisted in days,
   destroying the system's only asset).
2. **No autonomous spending.** Agents may not purchase, subscribe, or transact.
3. **No live trading and no moving of funds.** Not this project's model anyway.
4. **No credential entry by agents.** Account creation and logins are owner's hands.

---

## RESEARCH FINDINGS — 2026-09-10 (verified, with sources)

**The zero-budget compliant path EXISTS.**

- **EmailOctopus free tier: 2,500 contacts, 10,000 emails/month**, their logo on
  emails/landing pages. Affiliate links accepted. Policy: no content from
  legitimate industries is expressly prohibited, provided subscribers consented.
  Affiliate marketing IS subject to extra scrutiny at account review.
  Source: help.emailoctopus.com/article/277-is-my-content-allowed
- EmailOctopus is reported as notably relaxed toward crypto AND affiliate senders,
  where Mailchimp and most mainstream ESPs restrict them.
- Purchased / scraped / rented / swapped lists = banned by the ESP itself. The
  opt-in-only limit is not merely our policy, it is contractually enforced.
- Gmail / Yahoo / Microsoft now enforce SPF + DKIM + DMARC. Gmail moved from
  warning to outright BLOCKING non-compliant senders in late 2025. Authentication
  is mandatory, not optional.

**PLATFORM RISK -> ARCHITECTURE DECISION.** Crypto senders are reported to get
suspended mid-campaign and lose their entire subscriber list. Therefore:
**the subscriber list is owned locally in SQLite; the ESP is only a sending pipe.**
A ban must cost a pipe, never the asset. This holds for whichever niche is chosen.

Still unresolved:
- Free sending domain = weak deliverability + no DKIM/DMARC control. Needs a path.
- Which free traffic channels actually convert. Agents to research.
- PC sleep kills always-on agents; 24/7 needs a host. Free options unverified.

---

## OPEN QUESTIONS FOR OWNER

- Is P0 phase 0 of a larger program, or the project name itself?
- Geography/audience: RU-facing, EN-facing, or global?
- Is the owner's identity attached, or does this run as an unattached brand?
