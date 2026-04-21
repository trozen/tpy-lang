from tpy import Int32


class Foo:
    val: Int32

    def __init__(self, v: Int32) -> None:
        self.val = v


def name_of(x: Foo) -> str:
    return "pkg_a"
