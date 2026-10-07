# A nullable borrow-tuple return takes its bare twin's element rules: a
# member rooted in a local would dangle, so it is refused with the bare
# twin's diagnostic (`tuple/error_tuple_ref_dangling`).
from tpy import int32


class Box:
    def __init__(self, n: int32) -> None:
        self.n = n


def pick(ok: bool) -> tuple[Box, int32] | None:
    a = Box(1)
    if ok:
        return (a, 1)  # tpyc: error(/Cannot return local or temporary as tuple element 0/)
    return None


def main() -> None:
    t = pick(True)
    if t is not None:
        print(t[0].n)


main()
