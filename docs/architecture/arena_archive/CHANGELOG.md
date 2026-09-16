# mx-arena: Development Changelog

These archived documents trace the arena's evolution from D128 to D141.
The current system documentation is `docs/architecture/MX_ARENA.md`.

## Document Index

| File | Date | What it captured |
|------|------|------------------|
| `MX_ARENA_ORIGINAL.md` | D129-D138 | The original doc, updated through Sprint 6. Contains early architecture descriptions and the pre-bug-fix results that were later invalidated. |
| `MX_ARENA_COMPLETE.md` | D137 | 578-line comprehensive doc at Sprint 5 state. 20 sections covering every component. Superseded by DEFINITIVE at D140. |
| `MX_ARENA_ASSESSMENT.md` | D131 | First external fidelity assessment. Identified journal replay vs decision replay gap. Established the 7/10 baseline score. |
| `MX_ARENA_ASSESSMENT_D131B.md` | D131 | Second assessment post-documentation update. Confirmed 7/10 unchanged. Identified data acquisition as highest priority. |
| `MX_ARENA_COMPREHENSIVE_AUDIT.md` | D132 | Deep code-level audit that found 5 critical bugs including the spread timezone showstopper. Defined the Sprint 1-5 roadmap. |
| `MX_ARENA_FINAL_ASSESSMENT.md` | D137 | External assessment correcting self-score from 9.5 to 8.5. Identified 6 innovations. Contains the walk-forward noise analysis. |
| `MX_ARENA_ROADMAP_TO_10.html` | D133 | Visual HTML sprint tracker with progress bars. |

## Key Moments in the Journey

**D129 (7.0):** Core exchange built. First validation showed +$16.94 on Mar 26.

**D133 (8.0):** Spread timezone bug found. P&L dropped to +$0.12. **99.3% was phantom.** This is the most important number in the project.

**D136 (8.5):** Walk-forward showed 3.43x overfit. Prevented deploying fragile parameters.

**D138 (9.0):** 6 innovations built. Capture ratio revealed 15-19% (80%+ left on table).

**D139 (9.0):** Three architectural findings: LLM=zero value, +1072% pipeline inversion, 41% phased stops.

**D140 (9.0):** Production changes deployed. Pipeline inversion + phased stops live.

**D141 (9.5):** All 5 gaps closed, all 10 innovations built, 524 synthetic candidates.
