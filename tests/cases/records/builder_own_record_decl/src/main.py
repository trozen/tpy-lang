# A builder method returning Own[Inner] is captured into an owned-record local,
# then consumed by move into a sink. @nocopy Inner forces the move (a silent
# copy would fail to compile); sink mutates and returns, so the value crossing
# the decl+move boundary is observed.
from tpy import Int32, Own, nocopy


@nocopy
class Inner:
    v: Int32

    def __init__(self, v: Int32) -> None:
        self.v = v

    def bump(self) -> None:
        self.v += 1


class Builder:
    seed: Int32

    def __init__(self, seed: Int32) -> None:
        self.seed = seed

    def build(self) -> Own[Inner]:
        return Inner(self.seed)


def sink(x: Own[Inner]) -> Int32:
    x.bump()
    return x.v


def run() -> None:
    b = Builder(41)
    r = b.build()
    n = sink(r)
    print(n)


run()
