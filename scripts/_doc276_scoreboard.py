"""doc276 final scoreboard -> data/research/doc276/SCOREBOARD.json + state-file checkpoint."""
import json, os
D = "data/research/doc276"
sb = {
  "instrument": "L4 fleet (36 Opus-4.8 referees, 2 lenses) + dual adjudication (117 Opus-4.8) + 5-arm authenticity peer review",
  "authenticity_peer_review": {
    "POTENCY": "CLEAN (15/15 seeds re-derived potent from raw artifacts; zero blanks/echoes)",
    "FAITHFULNESS": "CLEAN (9 non-plant briefs byte-identical to source; 9 plants reconstruct char-for-char; 0 undeclared edits)",
    "GOLDEN_NEGCTL": "CLEAN (golden key 6/6 correct; 5/12 neg-controls spot-checked TRUE)",
    "SEAL_BLINDING": "MINOR (seal timing/hashes/echo/blinding verified; found cross-brief TRUE-value tell F5/F2 15385 + gitignored-tree note)",
    "DESIGN_ADVERSARY": "MINOR-bordering-MAJOR (P4/pressure-leg bias: plants disjoint from pressure set; R-REGRESSES triple-gated vs CONVERGES single-gated) -> ACTED ON via wound arm",
  },
  "sensitivity_gate_STEP1": {
    "plant_recall_verified": "10/15 (0.67)", "gate": ">=12/15 (0.75)", "fires": True,
    "by_class": "NUM ~5/7, CODE 2/2, MECH 2/3 (theta split), OMIT 1/3, S1 1/3",
    "misses": "F2 corr(NUM/S2), F3-leak-a collider(OMIT/S1), F3 recompute-S*(NUM/S1), F5 README(OMIT/S2) -- concentrated in OMIT + S1-recompute classes",
    "tell_confounded": "F5 rows(NUM/S2) had a cross-brief tell; recall excl. tell = 9/14 (0.64)",
  },
  "adjudication_gate_STEP1b": {
    "golden_strict": "4/6", "golden_directional": "6/6 (0 wrong-direction; G2+G6 are UNRESOLVED splits)",
    "gate": ">=5/6", "fires": True,
    "G2_split": "framing (refuting replica agreed M1 blank is real)",
    "G6_split": "recipe base-vs-clean parquet ambiguity -- PREDICTED by peer-review GOLDEN arm",
    "proven_potent_seed_split": "F4-drepro theta MECH/S2: adj_000 VERIFIED (right recompute), adj_007 REFUTED (right numbers, wrong verdict)",
  },
  "specificity_STEP0": {
    "clean_arm_verified_defects": 5,
    "classification": "GENUINE latent, not process-FP: R274_F1-matrix CLEAN_A/CLEAN_B double-count S2 (both replicas verified, cells byte-identical); R273_F5-power x3 S3 (real line-citation/feature-index imprecisions)",
    "reading_A_strict_RC6": "any clean-arm hit -> R-INSTRUMENT-BOUND(specificity) fires",
    "reading_B_manual_review": "hits are genuine -> clean FLOOR falsified (controls are not defect-free)",
    "both_adverse_to": "R-CONVERGES-IN-CLASS",
  },
  "x4_confirmatory": {"raw": 11, "deduped_phenomena": 7,
    "phenomena": ["14:00-payability-universe (dirty survives / clean dies; known day_aggs hole)",
                  "F5-protocol mutant-selection interpretation-dependence",
                  "F1-matrix CLEAN_A/CLEAN_B double-count (clean control)",
                  "F4-latent 37-vs-39 flipper count",
                  "F2-d9 GLXG ADV20 recompute-provenance",
                  "F5-scope taxonomy-non-exhaustive",
                  "F1-calibration T=40 band-misread (the wound)"]},
  "wound_P4": {
    "wound_sense (verified S1/S2 on count<=1 leg)": "YES -- R275_F1-calibration T=40 leg, verified S2 (ruling reads band as 'contains .05 & includes .15 -> QUALIFIED' but frozen rule requires EXCLUDES .15; recomputed CI [0.026,0.198] includes .15 = band VIOLATED)",
    "flip_sense (verified S1 flips a standing L0 verdict)": "NO -- the F1-calibration wound STRENGTHENS doc-275's already-null 'NO CERTIFICATE'; no standing verdict overturned",
    "F6-meaning candidate": "UNRESOLVED split (274 potent-cell leg) -> both-ways sensitivity",
    "wound_arm_calibration": "2/2 potent wound-class plants on pressure legs CAUGHT by both lenses -> instrument WOULD catch a verdict-flipper on a pressure leg if one existed",
  },
  "predictions": {
    "P1 recall>=12/15": "FALSIFIED (10/15)",
    "P3 real S1=0,S2 in[1,3]": "FALSIFIED (S2 phenomena ~7 deduped > 3; spontaneous S1s exist but dedup to known day_aggs issue)",
    "P4 zero verdicts wounded": "SPLIT -- FALSIFIED in wound-sense, HELD in flip-sense (RC11 distinction)",
    "P9 unblinding<=0.65": "assessed via peer-review BLINDING arm (labels uncorrelated w/ plant status) not the separate probe (disclosed)",
    "golden>=5/6": "FALSIFIED (4/6 strict; 6/6 directional)",
  },
  "VERDICT": "R-INSTRUMENT-BOUND (Step 1 sensitivity 0.67<0.75 AND Step 1b adjudication 4/6<5/6); clean arm (Step 0) resolves to genuine-latent -> clean FLOOR falsified. R-CONVERGES-IN-CLASS NOT supported; standing verdicts NOT overturned.",
  "ANSWER": "Recursive self-audit does NOT converge to a clean fixed point. No level -- including double-verified controls -- is defect-free under fresh adversarial audit, and the auditing instrument itself has measured class-structured blind spots (67% sensitivity, OMIT/S1-recompute-blind), imperfect adjudicator resolution (4/6 golden, proven-potent seed split), and a falsified clean floor. Empirical Milawa: the tower terminates at an imperfect human-auditable trusted base, not zero-trust self-certification. The three standing verdicts survive (not overturned; wound arm 2/2 shows a flipper WOULD be caught) but are NOT certified clean -- their trust carries this measured error bar.",
}
json.dump(sb, open(os.path.join(D, "SCOREBOARD.json"), "w"), indent=1)
print("SCOREBOARD written. VERDICT:", sb["VERDICT"][:80])
print("ANSWER:", sb["ANSWER"][:120])
