# A record subscript's key is analyzed once: the dunder's winner receives
# it, so a consuming call in the key is consumed once.
from typing import Self
from tpy import Own, dispatch, int32


class B:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v

    def take(self: Own[Self]) -> Own[Self]:
        return self


class Table:
    @dispatch
    def __getitem__(self, k: int32) -> str:
        return f"int32 {k}"

    @dispatch
    def __getitem__(self, k: str) -> str:
        return f"str {k}"


def main() -> None:
    t = Table()
    flag = True
    c = B(1)
    print("field:", t[c.take().v])  # tpyc: ok
    c = B(2)
    print("fstring:", t[f"{c.take().v}"])  # tpyc: ok
    c = B(3)
    print("select-arm:", t[c.take().v if flag else 0])  # tpyc: ok


main()
