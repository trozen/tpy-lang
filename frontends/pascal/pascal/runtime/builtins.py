"""Pascal-runtime helpers for built-ins that need real work (not just
a translator desugar).

M10 ships:
  - `random_real()` / `random_int(n)` -- Pascal's `random` overloads.
  - `randomize()` -- seeds the global RNG with OS entropy.
  - `succ_int(x)` / `pred_int(x)` -- the integer-typed forms of
    Pascal's `succ` and `pred`. The translator routes enum-typed
    succ/pred to enum-specific helpers when those land in a later
    milestone; for now `succ(c)` on a `Color` errors at translate
    time.
"""

from __future__ import annotations

import random as _random
from tpy import Int32


def random_real() -> float:
    """`random` (no args) -- uniform float in [0.0, 1.0)."""
    return _random.random()


def random_int(n: Int32) -> Int32:
    """`random(n)` -- uniform int in [0, n)."""
    return _random.randrange(n)


def randomize() -> None:
    """Seed the global RNG with OS entropy (Pascal `randomize`'s
    accepted modern interpretation -- the original DOS variant used
    the system clock)."""
    _random.seed()


def succ_int(x: Int32) -> Int32:
    return x + 1


def pred_int(x: Int32) -> Int32:
    return x - 1


def check_subrange(value: Int32, lo: Int32, hi: Int32, name: str) -> Int32:
    """Assert `value` lies in `[lo, hi]`, returning it untouched. TP7
    raises a range-check error (code 201) when an out-of-range value
    is assigned to a subrange-typed variable. The translator inserts
    a call to this helper at every assignment site whose target type
    is a subrange alias, so the panic surfaces close to the bad
    write."""
    if value < lo or value > hi:
        raise RuntimeError(
            f"range check error: value {value} out of range "
            f"[{lo}..{hi}] for {name!r}"
        )
    return value
