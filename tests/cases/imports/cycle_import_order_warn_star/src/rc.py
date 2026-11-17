# A star import of a star import: `rm` star-imports the suspended `rc` and
# is itself suspended when `rn` star-imports it, so CPython's `rn` gets only
# what both had bound: C1 and M1.
class C1:
    def __init__(self, v: int) -> None:
        self.v = v


import rm  # noqa: E402


class C2:
    def __init__(self, v: int) -> None:
        self.v = v


def main() -> None:
    print("nested star:", rm.total(), rn.grand_total())


import rn  # noqa: E402
