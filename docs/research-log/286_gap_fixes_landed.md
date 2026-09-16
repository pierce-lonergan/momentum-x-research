# 286 — THE GAP FIXES, LANDED: everything doc-285 found, fixed and validated for tomorrow's launch

**Author**: Claude (Fable 5, ultracode; 4 fix-builders + 3 adversarial validators, wf_10633130, ~1.0M tokens) | **Date**: 2026-07-06 | **Class**: HOT-PATH FIXES (validated, tested, inert until the 4:30 restart) + ENV CORRECTIONS + THE MISSING SENTINEL | **Mandate (Pierce)**: "comprehensive fixes to the gaps… make sure they land tomorrow."

> Every gap doc-285 surfaced is now fixed, staged, or scheduled — and the new sentinel *proves itself* by firing 9 breaches on today's own session. All hot-path fixes are strictly conservative (protect more, size less, never trade more), each carries tests **verified to fail on the pre-fix code**, and each passed an adversarial validator that mutated the fix to prove the tests bite.

## §1 — What lands at tomorrow's 4:30 launch

**A — D98 absence-confirmation (the phantom-close fix; gap #2).** A failed `get_positions()` now yields UNKNOWN — the stop-out scan *skips the cycle* ("broker state unknown"), never diffs against a fabricated empty list. Booking a stop-out now requires the tracked stop order to report `filled` via the orders API (booked at its **real filled_avg_price**, not the intended stop) or two *consecutive successful* snapshots both absent (streaks reset on any failure/reappearance, and — validator's catch — on position-episode boundaries, so a stale streak can never fast-confirm a new position). Worst case is one poll cycle of delay; phantom action is structurally impossible. 19 new tests, **19/19 fail on pre-fix HEAD** (proven in a scratch worktree). Yesterday's 10:48 sequence replayed under the fix: zero phantom closes, $0 fake P&L.

**B — Book integrity (gaps #10/#11).** Root cause found: *no path anywhere* decremented `ManagedPosition.qty` on partial sells — the same stale qty then made `attach_external_stop` refuse the D313 write-back that already existed. Fixed: confirmed partial fills book qty down exactly once (weakref registry, terminal-status-keyed, double-fire-proof); the watcher's emergency-stop oid is written back onto the position (guarded, race-safe) so recon, D98, and the resubmitter finally share one view of protection. 17 tests.

**C — Stale-anchor, strictly conservative (gap #9).** Entry qty is now capped against the **worst legal fill price** — `max(eval, marketable limit)` — so a 5% cap can never fill at 5.4% (RIVN's exact numbers are a regression test: 492 shares, not 1,552). Tranche targets **re-anchor to the actual fill**, so a chased entry can't collapse T1 to 0.7%-away and clip in 4 minutes. Validator-proven: no branch can increase size. 7 tests, 4 fail on old code.

**D — The config-truth recon (the meta-gap, #6/#12) + the LLM fix (#5).** `scripts/config_truth_recon.py` (wired into the nightly, best-effort): per-fill notional vs intended cap, risk-at-stop vs intended risk, T2 split vs setting, exit-policy *applied* vs intended, **user-scope Windows env enumeration** (the invisible surface), and instrument-freshness (rocket ledger / posture trend / Kalshi / red-flag). **Proof: run on today it fires 9 breaches — every one a thing the manual forensic had to find by hand.** 27 tests. Plus: `src/agents/base.py` now emits a CRITICAL `LLM_PRIMARY_MODEL_DEAD` incident after 5 consecutive provider rejects (fallback success no longer silences it — today's exact failure mode), and the dead Tier-1 model is replaced (`LLM_TIER1_MODEL=Qwen/Qwen3-235B-A22B-Instruct-2507-tput`, verified serverless with a live call; streaming-only and empty-content candidates rejected).

## §2 — Env corrections (verified through Settings; backups kept)

- **Corrected size package** (`.bak-doc285`): `EXEC_TIER1/2/3_POSITION_PCT=0.05` + `KELLY_TIER1..4_RISK_PCT=0.0067` — the knobs the executor *actually* reads (doc-285 proved the doc-282 knobs were dead letters).
- **`MOMENTUM_EXIT_POLICY` user-scope var REMOVED** (operator-authorized) → default `bar1_legacy`, per doc-280's verdict (overnight carry = the one robustly negative arm). The recon now watches user-scope env permanently.
- **`LLM_TIER1_MODEL`** replaced (above).

## §3 — Scheduled and verified

- **16:45 today (one-shot, self-deleting)**: tag the two phantom stop-out rows in `trade_results.jsonl` as `infrastructure_contaminated` (gap #7) — runs after the EOD writer finishes; idempotent.
- Combined new-test run: **108/108 green**; full-suite delta running (baseline = the known pre-existing debt, chipped `task_b24afcae`); py_compile clean on every touched file.
- Tonight: rocket-gate forward ledger's first true forward row + posture scoreboard append + Kalshi 18:00 run — all now freshness-watched by the recon.

## §4 — Tomorrow's bot vs today's

| Surface | Today (7/6) | Tomorrow (7/7) |
|---|---|---|
| Position cap | 15–50% tiers (RIVN 15.65%) | **5% hard, priced at worst legal fill** |
| Risk/trade | 2% Kelly tier | **0.67% all tiers** |
| Overnight carry | t1_next_open (hidden env) | **bar1_legacy** (doc-280-validated) |
| Broker blackout | fabricates "flat" → phantom closes | **skips the cycle, books nothing** |
| Partial sells | qty never decremented | booked exactly once |
| Emergency stops | invisible to recon | oid written back — one shared view |
| Tranche targets | off frozen eval → 4-min clips | re-anchored to fill |
| Tier-1 LLM | dead, silent | alive; dies loudly (CRITICAL incident) |
| Config drift | invisible | **nightly config-truth recon** |

The same guardrail philosophy that saved today (shadow-mode recon, GTC stops, the T2 halving) is now backed by code that can't fabricate absence, can't out-size its cap, can't lose its own book, and can't drift silently. **The posture Pierce ordered finally trades tomorrow — and the machine now checks, every night, that it actually did.**
