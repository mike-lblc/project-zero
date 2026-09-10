# P0 — TODO

## Blocked on owner
- [ ] Jurisdiction / country for affiliate signup + exchange KYC — decides which
      programs will even accept the application. Blocks G2 verification.
- [ ] Brand identity: owner's name attached, or unattached brand persona?
- [ ] Is "P0" phase 0 of a program, or the project's name?

## P0.1 — SPINE: DONE (2026-09-10, all verified running)
- [x] Ollama alive. Models present: qwen3.5:9b (6.6GB, FITS VRAM — chosen),
      qwen3.5:4b (unreliable), qwen3-coder:30b (18GB, won't fit)
- [x] `brain.db` — 16 tables incl. evidence/sources, council, bus, proof ledger
- [x] core/db.py, core/eo.py, core/router.py, core/guard.py
- [x] EmailOctopus client verified HTTP 200 (UA fix for Cloudflare 1010)
- [x] Router ENFORCED: judgment tasks refuse to run locally -> escalate
- [x] Guard ENFORCED: BLACK actions blocked, daily caps in code, kill switch
- [x] .gitignore written FIRST — .env can never be committed
- [x] Local model round-trip: 12.4s cold, replied 'ready'

## Next (P0.2 — first real agent job)
- [ ] **VERIFY x402 / Agent.market payout gate**: does earning require a KYC'd
      Coinbase account, or is the payment layer permissionless? This is the
      Binance lesson — verify before building toward it. FIRST Scout job.
- [ ] Scout agent: search + fetch -> evidence rows with sources
- [ ] Adversary pass as second prompt (not a second service)
- [ ] Owner decision: turn ON double opt-in for list 'Audience'

## Then
- [ ] P0.2 Scout agent
- [ ] P0.3 Verifier + Scorer
- [ ] P0.4 Adversary
- [ ] P0.5 Decision report

## Verified already (2026-09-10, do not re-research)
- [x] Free compliant ESP path exists: EmailOctopus 2,500 contacts / 10,000
      emails per month, affiliate links accepted, opt-in enforced
- [x] Crypto payout rails confirmed: Binance (no minimum, real-time USDT/BTC/BNB),
      Nexo, Bitunix, Trezor
- [x] Hardware: RTX 5070 Laptop 8 GB VRAM, 31.7 GB RAM, i9-14900HX, 471 GB free
- [x] Bybit affiliate EXCLUDED under Rule Zero (pre-existing account)


## P0.2 STATUS — 2026-09-10 (rail research complete)

### RESOLVED
- [x] x402 protocol is PERMISSIONLESS at protocol layer (ev#2) — the Binance-style
      jurisdiction trap does NOT apply to the protocol itself
- [x] NO-KYC facilitator EXISTS and is reachable from owner's network:
      OpenFacilitator pay.openfacilitator.io — no signup/account/rate limits (ev#4)
- [x] Wallet stored. **ETH/Base 0xECa8...5354 is the usable one.**
      **BTC bc1q...g4hr CANNOT receive x402** — wrong rail entirely (ev#5)
- [x] Market is REAL and readable with NO auth: CDP Bazaar = **14,268 services**,
      Base dominant, prices $0.001-$0.05/call, modal $0.001 (ev#6)
- [x] HOSTING solved with NO new account: localtunnel (npx, present) or
      cloudflared quick tunnel (winget). Public HTTPS, no signup.
      GitHub Pages CANNOT serve HTTP 402 — it is static.

### BLOCKING FORK (objection #4, unresolved)
- [ ] **No-KYC facilitator has a DEAD marketplace.** OpenFacilitator third-party
      volume ~$708 EVER; top third-party earner $111; quiet ~4 months (ev#7).
      The live market is on Coinbase CDP, whose SELLER listing likely needs a CDP
      account = jurisdiction gate returns.
      MUST DETERMINE: can a seller list on CDP Bazaar without a KYC'd account?
      If NO -> either accept a dormant marketplace, or find a third path.

### THEN
- [ ] Choose the product: what will an agent actually pay $0.001-$0.05 for?
- [ ] Build endpoint + x402 middleware, point payTo at the ETH address
- [ ] Bootstrap listing (Bazaar indexes only AFTER first confirmed settle)
