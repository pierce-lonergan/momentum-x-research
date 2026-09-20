"""Epistemic machinery: how this program decides what to ask and what a closure means.

Three pieces, each addressing a failure this program actually committed:

* `closure`  — the discrimination taxonomy. A closure must say whether it is a
  measurement of the market or of our own instruments (doc 296 was filed as a
  result when it was a spec mismatch).
* `archive`  — the stepping-stone archive. Closures keep the keystone that was
  missing, so an arriving capability can mechanically re-open them (doc 278 and
  doc 295 both did this by hand, by luck).
* `eig`      — experiment selection by expected information gain, priced against
  the multiplicity toll each trial levies on every other hypothesis in the
  queue (nothing priced a trial before it ran).
* `retro`    — the retro-validation engine and the first-principles gate.

Reference: docs/research-log/299_the_epistemic_architecture.md
"""

from .closure import KEYSTONES, Closure, ClosureRecord, Keystone
from .archive import Archive, SteppingStone
from .eig import Design, appraise, diversity_bonus, operative_bar, rank, shannon_entropy
from .retro import find_revivals, first_principles_gate, triage

__all__ = [
    "KEYSTONES",
    "Archive",
    "Closure",
    "ClosureRecord",
    "Design",
    "Keystone",
    "SteppingStone",
    "appraise",
    "diversity_bonus",
    "find_revivals",
    "first_principles_gate",
    "operative_bar",
    "rank",
    "shannon_entropy",
    "triage",
]
