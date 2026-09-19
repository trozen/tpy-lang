# A @property read as the RECEIVER of an augmented assignment target. The
# render would spell the getter on both sides of `b.rec().x = add_check(
# b.rec().x, 1)`, calling it twice where Python calls it once. That is one
# site of the evaluation-order class, a postponed language decision, so the
# accessor spelling is held at a reject rather than fixed alone; the spelled-
# method twin `b.rec_m().x += 1` keeps the double evaluation it has, which is
# why this case pins only the property spelling. See
# BUGS.md#augassign-call-receiver-double-eval.
# A subscript's INDEX is held for the same reason -- `xs[b.i] += 1` spells the
# whole target twice -- but only ONE error can be the first, so that leg is not
# a section here; its tripwire is the `augassign_index__n__prop__*` cells of
# scripts/thir_migration/review/property_position_sweep.expected.json, beside
# the `__meth__` cells that keep compiling.
from tpy import int32


class Rec:
    x: int32

    def __init__(self) -> None:
        self.x = 1


class B:
    _r: Rec

    def __init__(self) -> None:
        self._r = Rec()

    @property
    def rec(self) -> Rec:
        return self._r

    def rec_m(self) -> Rec:
        return self._r


def main() -> None:
    b = B()
    b.rec_m().x += 1
    b.rec.x += 1  # tpyc: error(/augassign.recv.accessor_double_eval/)
    print("x:", b._r.x)


main()
