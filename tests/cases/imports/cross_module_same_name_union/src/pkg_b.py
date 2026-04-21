from tpy import Int32


class Foo:
    count: Int32

    def __init__(self, c: Int32) -> None:
        self.count = c


class Bar:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n


def name_of_b() -> str:
    # Uses pkg_b's own Foo to exercise a same-named record whose
    # identity must stay distinct from pkg_a.Foo in the shared
    # compilation.
    f = Foo(Int32(99))
    _ = f.count
    return "pkg_b"
