# A comprehension variable is a fresh binding of the comprehension's own
# scope: it hides the enclosing name's form and facts inside, and the
# enclosing binding is untouched after it. Core positions; the rest in parts.
from typing import Iterator, Optional, Self
from tpy import Own, int32
from gmod import G
import part_positions
import part_shapes
import part_facts
import part_closures
import part_temps
import part_handover

# Module level: the source `x` is the global, read outside the clause.
x = [10, 20, 30]
s = [x + 1 for x in x]  # tpyc: ok
# A pointer-slot Optional global hidden by the variable.
OG: Optional[int32] = 5
og = [OG + 1 for OG in [1, 2]]  # tpyc: ok
# A module-level filter walrus binds the module global.
W: int32 = 0
w = [v for v in [1, 2, 3] if (W := v) > 1]  # tpyc: ok


class OP:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


def own_pairs() -> Iterator[tuple[Own[OP], int32]]:
    yield (OP(1), 10)
    yield (OP(2), 20)


# A module-level head target taking its element over holds it in the step.
owned_head = [a.x + b for a, b in own_pairs()]  # tpyc: ok


COPIES: list[int32] = []


class Hooked:
    def __init__(self, n: int32) -> None:
        self.n = n

    def __copy__(self) -> Own[Self]:
        COPIES.append(self.n)
        return Hooked(self.n + 100)


def hooked_pairs() -> Iterator[tuple[Own[Hooked], int]]:
    yield (Hooked(5), 1)
    yield (Hooked(6), 2)


# A module-level for head over a handed-over step: the global target takes
# the member over, no copy (the `int` member makes the holder worth a
# const reference).
hooked: list[int32] = []
for hk, hi in hooked_pairs():  # tpyc: ok
    hooked.append(hk.n)
# Neighbour: the comprehension head.
hooked_comp = [hc.n for hc, hj in hooked_pairs()]  # tpyc: ok


def hidden_opt_local(o: Optional[int32], xs: list[int32]) -> None:
    v = o
    # The variable is the element, not the outer Optional.
    if v is not None and any([v > 0 for v in xs]):  # tpyc: ok
        print("opt_local", v + 1)


class OptN:
    n: Optional[int32]

    def __init__(self, n: Optional[int32]) -> None:
        self.n = n


def hidden_opt_param(v: Optional[int32], xs: list[int32]) -> None:
    # An Optional parameter hidden by the variable.
    if v is not None and any([v > 0 for v in xs]):  # tpyc: ok
        print("opt_param", v + 1)


def hidden_opt_capture(o: OptN, xs: list[int32]) -> None:
    match o:
        # A match capture of an Optional field hidden, in the guard...
        case OptN(n=v) if v is not None and any([v > 5 for v in xs]):  # tpyc: ok
            print("opt_guard", v is None)
        case OptN(n=v):
            # ... and in an `if` of its arm (a guard's own `is not None`
            # narrows nothing in the arm: BUGS.md#match-guard-facts-not-applied).
            if v is not None and any([v > 0 for v in xs]):  # tpyc: ok
                print("opt_capture", v + 1, [v * 2 for v in xs])  # tpyc: ok
            else:
                print("opt_capture none")


def param_inverse(v: int32, xs: list[int32]) -> None:
    # A hidden parameter: the outer one reads as before after.
    r = [v * 2 for v in xs]  # tpyc: ok
    print("param", r, v)


def self_shadow(xs: list[int32]) -> None:
    r = [xs + 1 for xs in xs]  # tpyc: ok
    xs.append(9)
    print("fn_self_shadow", r, xs)


def imported(xs: list[int32]) -> None:
    # The variable hides an imported global: reads see the element.
    print("imported", [G + 1 for G in xs], {G + 1 for G in xs}, G)  # tpyc: ok


def nested(rows: list[list[int32]]) -> None:
    # The inner clause hides the outer clause's variable.
    print("nested", [[x * 10 for x in x] for x in rows])  # tpyc: ok
    print("nested_filter", [[x for x in x] for x in rows if len(x) > 1])  # tpyc: ok
    rows[0].append(5)
    print("nested_after", rows)


def nested_narrow(opts: list[Optional[int32]], ys: list[int32]) -> None:
    # The filter narrows the outer clause's x, which the inner clause hides.
    print("nested_narrow", [[x + 1 for x in ys] for x in opts if x is not None])  # tpyc: ok


def main() -> None:
    x.append(40)
    print("module", s, x, og, OG, w, W, owned_head)
    print("module_hooked", hooked, hooked_comp, COPIES)
    hidden_opt_local(5, [0, 3])
    hidden_opt_local(None, [1])
    hidden_opt_param(4, [1])
    hidden_opt_param(None, [1])
    hidden_opt_capture(OptN(6), [2])
    hidden_opt_capture(OptN(6), [7])
    hidden_opt_capture(OptN(None), [2])
    nested_narrow([1, None, 2], [10])
    param_inverse(3, [1, 2])
    self_shadow([1, 2])
    imported([1, 2])
    nested([[1, 2], [3]])
    part_positions.main()
    part_shapes.main()
    part_facts.main()
    part_closures.main()
    part_temps.main()
    part_handover.main()


main()
