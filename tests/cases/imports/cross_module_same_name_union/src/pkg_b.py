from tpy import int32


class Foo:
    count: int32

    def __init__(self, c: int32) -> None:
        self.count = c


class Bar:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def name_of_b() -> str:
    # Uses pkg_b's own Foo to exercise a same-named record whose
    # identity must stay distinct from pkg_a.Foo in the shared
    # compilation.
    f = Foo(int32(99))
    _ = f.count
    return "pkg_b"
