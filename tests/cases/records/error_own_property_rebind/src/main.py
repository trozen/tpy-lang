# TRIPWIRE for a LOST ADMISSION, not a rule: binding an `Own[...]` getter
# result and then rebinding that local (`v = s.own` then `v2 = v`) has no
# render, although the FIRST binding one line up compiles -- a program that
# used to build and run no longer does, filed as
# BUGS.md#own-rebind-lost-admission. The METHOD twin rejects identically and
# always did, so the property joined its twin rather than diverging from it --
# which is why this is a tripwire: the day the by-value-accessor decl family is
# fixed (TODO.md, "A rebound local off an `Own[...]` accessor rejects where the
# first binding compiles"), this case must be converted, not re-baselined.
# WORKAROUND: use the first binding directly, or `v2 = copy(v)`.
from tpy import Own, int32


class Src:
    _xs: list[int32]

    def __init__(self) -> None:
        self._xs = [1, 2]

    @property
    def own(self) -> Own[list[int32]]:
        out: list[int32] = []
        for x in self._xs:
            out.append(x)
        return out

    def own_m(self) -> Own[list[int32]]:
        out: list[int32] = []
        for x in self._xs:
            out.append(x)
        return out


def use_prop(s: Src) -> None:
    v = s.own
    v2 = v  # tpyc: error(/decl.slot_type/)
    print(len(v2))


# the method twin, unannotated: the compile stops at the first error, and this
# leg is here to show the two spellings agree
def use_meth(s: Src) -> None:
    v = s.own_m()
    v2 = v
    print(len(v2))


def main() -> None:
    use_prop(Src())
    use_meth(Src())


main()
