from tpy import int32


class Foo:
    val: int32

    def __init__(self, v: int32) -> None:
        self.val = v


def name_of(x: Foo) -> str:
    return "pkg_a"
