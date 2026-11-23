# A handle passed to a callee that writes through it demotes its receiver,
# whatever the callee says of itself: a method inferred readonly (a fact
# about its own receiver), a `@readonly` method's view parameter (a value
# type, so not a readonly slot), a `@pure` function (trusted, not enforced).
from tpy import int32, Span, auto_readonly, pure, readonly


class Pad:
    xs: list[int32]

    def __init__(self) -> None:
        self.xs = [1, 2]

    @auto_readonly
    def view(self) -> Span[auto_readonly[int32]]:
        return self.xs


class Sink:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def poke(self, s: Span[int32]) -> None:
        # writes its argument, not itself: inferred readonly
        s[0] = 99

    @readonly
    def poke_ro(self, s: Span[int32]) -> None:
        s[1] = 98


@pure
def poke_pure(s: Span[int32]) -> int32:
    s[0] = 97
    return 0


def via_inferred(p: Pad, k: Sink) -> None:
    # inferred-readonly callee: `p` is mutable
    k.poke(p.view())  # tpyc: ok


def via_readonly(p: Pad, k: Sink) -> None:
    # declared @readonly callee, view parameter: `p` is mutable
    k.poke_ro(p.view())  # tpyc: ok


def via_pure(p: Pad) -> int32:
    # @pure callee: `p` is mutable
    return poke_pure(p.view())  # tpyc: ok


def main() -> None:
    p = Pad()
    k = Sink()
    via_inferred(p, k)
    via_readonly(p, k)
    print("writes:", p.xs[0], p.xs[1])
    via_pure(p)
    print("writes pure:", p.xs[0])
