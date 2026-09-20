#!/usr/bin/env python
"""CLI for the epistemic machinery.

    python scripts/epistemics.py discriminate
        What fraction of the graveyard is evidence about the market?

    python scripts/epistemics.py keystones
        Which missing capabilities block the most families?

    python scripts/epistemics.py anomalies
        Unexplained observations left behind by closures. The program's least
        crowded source of hypotheses, because nobody was looking for them.

    python scripts/epistemics.py revive --have KEY [KEY ...]
        Archived families whose stated blocker the given capabilities address.

    python scripts/epistemics.py bar --trials N --obs M
        The operative multiplicity bar, and what one more trial costs.

    python scripts/epistemics.py plan --spec designs.json
        Rank candidate experiments by entropy-regularised net information gain.

Reference: docs/research-log/299_the_epistemic_architecture.md
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.epistemics.archive import Archive  # noqa: E402
from src.epistemics.eig import Design, operative_bar, rank  # noqa: E402
from src.epistemics.retro import find_revivals  # noqa: E402


def _cmd_discriminate(args) -> int:
    arc = Archive(args.path)
    d = arc.discrimination()
    print(json.dumps(d, indent=2))
    if d["n"]:
        print()
        print(f"{d['market_evidence']}/{d['n']} closures ({d['market_evidence_frac']:.0%}) "
              "are measurements of the market.")
        print(f"{d['about_us']}/{d['n']} are measurements of our own instruments, "
              f"of which {d['revivable']} name a keystone and can be re-opened "
              "mechanically.")
    return 0


def _cmd_keystones(args) -> int:
    arc = Archive(args.path)
    rows = arc.keystone_census()
    if not rows:
        print("archive is empty")
        return 0
    width = max(len(k) for k, _, _ in rows)
    print(f"{'keystone'.ljust(width)}  blocks  families")
    print("-" * (width + 40))
    for key, n, fams in rows:
        print(f"{key.ljust(width)}  {n:>6}  {fams[0][:48]}")
        for f in fams[1:]:
            print(f"{' ' * width}          {f[:48]}")
    return 0


def _cmd_anomalies(args) -> int:
    arc = Archive(args.path)
    rows = arc.anomalies()
    if not rows:
        print("no anomalies recorded")
        return 0
    print(f"{len(rows)} unexplained observations left behind by closures:\n")
    for sid, fam, a in rows:
        print(f"[{sid}] {fam}")
        print(f"    {a}\n")
    return 0


def _cmd_revive(args) -> int:
    arc = Archive(args.path)
    revivals = find_revivals(
        set(args.have), archive=arc, allow_market_evidence=args.allow_market_evidence
    )
    if not revivals:
        print(f"nothing revives on {sorted(set(args.have))}")
        return 0
    print(f"{len(revivals)} archived families respond to "
          f"{sorted(set(args.have))}:\n")
    for r in revivals:
        flag = "FULLY UNBLOCKED" if r.fully_unblocked else "partially unblocked"
        print(f"[{r.stone.stone_id}] {r.stone.closure.family}")
        print(f"    score {r.score:.3f}  ({flag})")
        print(f"    {r.reason}")
        print(f"    revives on: {r.stone.revival_predicate}")
        print()
    print("Revival is a licence to RE-ASK, not to believe. Each of these must pass")
    print("the first-principles gate and then register as a fresh trial, which")
    print("raises the bar for everything else.")
    return 0


def _cmd_bar(args) -> int:
    before = operative_bar(args.trials, args.obs)
    after = operative_bar(args.trials + 1, args.obs)
    print(json.dumps({
        "n_trials": args.trials,
        "n_obs": args.obs,
        "n_years_implied": round(args.obs / 252.0, 2),
        "operative_bar": round(before, 4),
        "operative_bar_after_one_more_trial": round(after, 4),
        "toll_of_one_more_trial_sharpe": round(after - before, 4),
    }, indent=2))
    return 0


def _cmd_plan(args) -> int:
    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))

    def _design(d: dict) -> Design:
        # Underscore-prefixed keys are commentary. The spec is meant to be read
        # by a person as well as parsed, and a declared prior with no stated
        # reasoning is not worth much, so the notes travel with the numbers.
        fields = {k: v for k, v in d.items() if not k.startswith("_")}
        if "keystones_required" in fields:
            fields["keystones_required"] = tuple(fields["keystones_required"])
        return Design(**fields)

    designs = [_design(d) for d in spec["designs"]]
    history = spec.get("history", {})
    ranked = rank(
        designs,
        n_trials_registered=spec.get("n_trials_registered", args.trials),
        omega=spec.get("omega", args.omega),
        history=history,
        lam=spec.get("lam", args.lam),
        available_keystones=(
            set(spec["available_keystones"]) if "available_keystones" in spec else None
        ),
    )
    out = [{"score": None if s == float("-inf") else round(s, 4), **a.to_dict()}
           for a, s in ranked]
    print(json.dumps(out, indent=2))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--path", default=None, help="stepping-stone archive path")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("discriminate").set_defaults(fn=_cmd_discriminate)
    sub.add_parser("keystones").set_defaults(fn=_cmd_keystones)
    sub.add_parser("anomalies").set_defaults(fn=_cmd_anomalies)

    p = sub.add_parser("revive")
    p.add_argument("--have", nargs="+", required=True)
    p.add_argument("--allow-market-evidence", action="store_true",
                   help="include closures that are measurements of the market "
                        "(requires an argued market-structure change)")
    p.set_defaults(fn=_cmd_revive)

    p = sub.add_parser("bar")
    p.add_argument("--trials", type=int, required=True)
    p.add_argument("--obs", type=int, required=True)
    p.set_defaults(fn=_cmd_bar)

    p = sub.add_parser("plan")
    p.add_argument("--spec", required=True)
    p.add_argument("--trials", type=int, default=33)
    p.add_argument("--omega", type=float, default=0.25)
    p.add_argument("--lam", type=float, default=0.5)
    p.set_defaults(fn=_cmd_plan)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
