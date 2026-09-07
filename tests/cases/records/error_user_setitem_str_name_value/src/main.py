# A user `__setitem__` whose VALUE is a str NAME (not a literal): the
# non-literal source keeps its view-to-owned machinery, outside the
# literal-only row, so `j["k"] = v` is rejected.
from tpy import Int32


class Jar:
    n: Int32

    def __init__(self) -> None:
        self.n = 3

    def __getitem__(self, key: str) -> str:
        return "x"

    def __setitem__(self, key: str, value: str) -> None:
        self.n += 1


def use(v: str) -> None:
    j = Jar()
    j["k"] = v  # tpyc: error(/setitem.family/)
    print(j.n)


def main() -> None:
    use("v")


main()
