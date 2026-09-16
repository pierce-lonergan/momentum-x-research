# 249 — Data levers resolved (free-float blocked, news not significant). Ex-ante rocket SELECTION is comprehensively closed. The constructive pivot: selection → execution.

**Author**: Claude Opus 4.8
**Date**: 2026-06-04
**Mandate**: Pierce — "test both data levers" (the doc 246/248 register: free-float + catalyst/news).

## TL;DR
Both remaining levers are resolved against. **Free-float is hard-blocked** (no point-in-time free-float source exists for us). **Catalyst/news content is data-limited and not significant** (18.7% coverage; LORO top-5% lift +1.3pp, 95% CI [−0.8, +3.0]; 2024 still negative). With this, **ex-ante rocket SELECTION from everything observable or obtainable at the 9:50 open is comprehensively falsified** — structure, catalyst-type, L2, float, raw tape, SOTA architectures, free-float, and news content all fail the cross-regime money gate. The earned conclusion: **the rockets in this universe are predominantly no-news, no-distinguishable-structure squeezes whose 9:50 signature is shared with the violent faders; the right tail is ex-ante random conditional on observables.** The constructive path is to stop buying *selection* alpha and pivot to *execution* (harvest the oracle's +40% asymmetry by cutting faders fast and riding rockets on a broad, selection-free basket) + regime-timing (the forward shadow). No live change.

## Free-float — HARD-BLOCKED (no data)
Finnhub `profile2` returns only current `shareOutstanding` (no float field); `metric=all` has zero share-supply fields. That is the *same* current shares-outstanding doc 243 already tested negative on the rocket label — plus a point-in-time/staleness problem (for low-float gappers, dilution offerings are exactly what change the float, so a current snapshot is leaky for 2024-25 candidates). True free-float (locked-up/restricted shares excluded — the actual squeeze constraint) requires a premium vendor we don't have. **Nothing new to test.** (The doc-247 synthesis's "Finnhub `floatShares`" assumption was wrong for this plan.)

## Catalyst/news content — tested, NOT significant
Polygon `/v2/reference/news` per candidate, window [session_date−3d → 13:50Z] (prior-day AH + overnight + premarket, strictly ex-ante). No LLM needed — Polygon ships per-article sentiment; keyword tagging gives catalyst-type. Features: has_news, news_count, sent_pos/neg/net, kw_{fda,offering,ma,earnings,contract}.
- **Coverage 18.7%** (rocket 24% vs fader 19%) — **~76% of rockets have NO indexed news.** Most rockets are no-news squeezes; news *presence* barely separates.
- **The one faint signal:** when rockets have news, sentiment is ~43:1 positive vs faders' ~59:21 — but it is presence-conditional (only the 24%), and positive PRs are also the pump-and-dump signature.
- **LORO money test, GBM(25) vs GBM(25+news), top-5% realized EOD:** 2024 −1.7%→**−1.5%**, 2025 +1.5%→+2.5%, 2026 +10.2%→+9.7%; AUC essentially unchanged (news adds no ranking signal). **Pooled 2024+2025: −0.5% → +0.8%, paired +1.3pp, 95% CI [−0.8, +3.0] — includes 0.** 2024 remains negative. **Does not clear the gate.** Consistent with the low coverage and the no-news-squeeze nature of the tail.

## The comprehensive closure (every selection lever, cross-regime money gate)
| lever | doc | verdict |
|---|---|---|
| structural sub-population | 235 | homogeneous; no edge |
| catalyst-TYPE homogeneity | 240 | homogeneous; no edge |
| L2 / true-OFI | 244 | no lift over tick-rule |
| shares-outstanding float | 243 | redundant; no edge |
| 25 micro+tape features, every model class | 245/248 | top-slice loses cross-regime; TabPFN referee worst → capacity not the wall |
| **raw un-aggregated tape** (Deep-Sets) | 248 | AUC↑ but money↓ (CI-separated worse) — KILL |
| SOTA architectures (FT-T/SAINT/SSM/…) | 247 | logically barred (re-rank a falsified space) |
| **true free-float** | 249 | **no data source** |
| **catalyst/news content** | 249 | **+1.3pp, CI incl 0 — not significant** |
Every angle converges on the same truth. The oracle ceiling is +40% (the rockets are real and huge) but **ex-ante unreachable** — the information that separates a rocket from its lookalike fader is **not present at the 9:50 decision point** in anything we can obtain.

## The constructive pivot: selection → EXECUTION (and regime-timing)
Selection alpha is exhausted; this is where the evidence redirects effort:
1. **Execution / position-management (highest-value, selection-free).** If you cannot pick the rocket ex-ante but the oracle tail is +40%, the lever is the **asymmetry**: enter a *broad* basket of the gapper universe and let **exit discipline** do the work — cut the −7%/−35% faders fast, ride the rare +40%+ rockets (runner mode / chandelier trails, doc 178). A random-selection basket with a sharply asymmetric exit can be positive-EV even when selection is random — and the system already has this machinery. This is the natural next research bet (and it's *exit* research, which docs 230-239 already touched — revisit with the rocket-tail framing).
2. **Regime-timing (the forward shadow).** The one place a selection signal survived was the *current* regime (2026). The doc-246 shadow already tracks, with no capital, whether "now" stays rocket-favorable. A regime-on/off overlay (trade the basket only when the regime is live) is regime-*timing*, not selection — and the shadow is the instrument to validate it.
3. **Redirect to where realized P&L lives:** execution quality, exit optimization, reliability (the phantom-P&L class), and the live competition vs Gemini.

## Status
**No live change.** The entire selection program was falsified cheaply and rigorously (pre-registered gates, cost-ordered, cross-regime, AUC retired). This is a definitive, valuable negative: it tells us to stop paying for ex-ante rocket selection and move the effort to execution + regime-timing, where the +40% oracle asymmetry can still be harvested without solving the (apparently unsolvable) selection problem.
**Basis**: `scripts/build_news_features_doc249.py` (Polygon news, 10,254 candidates, 18.7% coverage) + the Finnhub free-float probe. **Predecessors**: 248 (architecture falsified), 246 (lever register), 245 (baseline), 243 (float). **Memory**: [[bet3-rocket-detection]].
