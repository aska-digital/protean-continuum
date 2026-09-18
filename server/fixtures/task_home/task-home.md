# Orda Task Home — open and ongoing work (single source of truth)

Update on every dispatch, worker completion, user decision.
The `overview-report` skill reads this file on "overview".

## RUNNING (background workers live)
- askasite-build-s3 (proc_2cc84c61f97e): staging deploy of passed fork + protean-kit PR. Next: audit re-QA → merge decision (review team).
- sym2p-report (proc_c07af224e0eb): final SYM-2P PDF + PR on protean-sym2p. Next: review-lane merge.
- pr111800-finish (proc_16d9e3a3656c): 111800 triple-check + HTML drafts. Next: owner reviews drafts → posting lane.
- evopet-floater-verify (proc_b1a331f0fe99): verify floater uncap + clip check. RECEIPT SAYS CLOSED (floater 126 = HUD 126). Confirm lane dir, then close.
- continuum-sync (proc_4faba8822b80): bidirectional TASK-HOME ↔ Continuum dashboard sync. Next: dashboard URL + round-trip proof.
- upstream-rebase (proc_11a87fba03eb): RECEIPT SAYS COMPLETE (4 PRs mergeable, pushes read back). Confirm lane dir, then close.
- kit-about (proc_c48ddef151ff): RECEIPT SAYS COMPLETE with 2 flags (homepage DNS, README six-vs-seven). Confirm, then close.
- typemon-publish (proc_97d6d34dcfad): RECEIPT SAYS COMPLETE (published + playable). One re-probe pending, then close.
- positioning-merge (proc_570af2d6e300): RECEIPT SAYS 8/8 merged. Verify zero-open, then close.
- bounce-sop-finish (proc_b5a051be0492): RECEIPT SAYS CLOSED (SOP law + org teams live). Confirm, then close.
- evopet-repo-verify (proc_b6ae151831c0): RECEIPT SAYS zero-PR proven. Confirm, then close.

## AWAITING OWNER (needs input, no worker running)
- VoiceStudio 4 HTML drafts (contrib/voicestudio/drafts/2026-09-17 - v0.5.3-contrib/): approve/drop per draft → posting lane; + local v0.5.3 install?; + pursue #2123/#2162 implementation?
- Homepage URL: proteus.askaconsult.com does not resolve (kit About points there). Give correct URL → correction lane.
- Local model fleet: Proteus consensus aggregator = local ornith-1.5-35b-a3b (35B, 143% CPU, 26GB at 09:01). Name the replacement + fanout on/off → config lane. Wider: repeated fallback/needs-decision across lanes (codex/gpt-5.6-luna, solar-pro4, step-3.7-flash) — needs fleet policy.
- Upstream 98616 review draft (contrib/hermes-agent/drafts/2026-09-17 - 01h08m/pr-98616-review.html): post or drop? 106742 resolved COVERED, no action.
- Meta-API PR drafts: unconfirmed whether pending session exists (candidates proteus 20260915_103804_64fa08 / 20260915_120307_99843f; upstream 111597/111612). Needs a search lane if still wanted.
- EvoPet manifest-snapshot env: 7 secrets missing (DATABASE_URL/R2 keys); schedule failures green-skipped until provided.
- Org hygiene: protean-team fork fate; team6-kit archival + pointer; protean-lcm transfer to org.
- Bounce SOP platform blockers: branch protection rules; shared commit identity.
- Phone test: ping both bots from phone; try a status ask.
- Handoff report + how-to guide: delivered in lane dir — surface exact paths on request.
- HUD glass (Tama #15 merged) + XP clamp (#16 merged): live sidecar still September build — needs install/restart to reach the screen. No lane yet.

## OPEN FOLLOW-UPS (tracked, no lane yet)
- Askasite out-of-slice: Desert Ant automatic-chain defect (--emit-items omits redacted); version drift (Team6 v1.2.0 vs kit v1.6.0); README six-vs-seven ingredient count (positioning lane may have caught).
- Raptora mission control: milestone integrated, demo evidence in lane — serve the demo link on request.
- SYM-2 report PDF delivered (/Users/kethuda/Documents/Proteus/protean-feed/sym2-protean-integration-report.pdf) with Phase-3 HOLD flag.
- Bots on orda profile stable since 02:49; stale lugia route fixed.
- TypeMon Pages: one stability re-probe post-publish.
- Pet died overnight once (cause undetermined, relaunched healthy) — watch for recurrence.

## CLOSED (2026-09-17)
- EvoPet uncap (pet 126 live) + floater 126 verified; EvoPet #25 + #26 merged; preview-snapshot spam guarded.
- Tama #15 (glass HUD) + #16 (XP 999) merged; HUD stale-pid fixed morning.
- Upstream 106742 fulfilled-by-maintainer + covered; 4 conflict PRs rebased mergeable.
- Overnight — 13 PRs merged (personal/org/EvoPet/Tama); board zero everywhere at last check.
- Positioning 8/8 merged; kit About live; bounce SOP law + org teams; TypeMon published + playable.
- VoiceStudio v0.5.3 incorporated (service still v0.5.2).
