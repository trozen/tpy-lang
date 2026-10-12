# A tuple local rebound from literals whose element is a `d.get(..)` result
# (a reference into the dict or the fallback, not an object of its own) has
# no binding route at a position the tuple owns: a located refusal naming
# the rewrite that compiles (BUGS.md#ptr-tuple-literal-element-rejects).
from tpy import int32


class P:
    def __init__(self, n: int32) -> None:
        self.n = n


def reseat(d: dict[str, P], fb: P) -> int32:
    t = (d.get("a", fb), 1)  # tpyc: error(/element 0 of the tuple bound to 't' here is a call result that is not an object of its own.*bind it to a name of its own first/)
    print(t[0].n)
    t = (d.get("zz", fb), 0)
    return t[0].n


def main() -> None:
    print(reseat({"a": P(5)}, P(1)))


main()
