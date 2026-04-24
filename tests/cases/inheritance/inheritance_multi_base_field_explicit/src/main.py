# Two bases each declare a same-name public field. The child accesses each
# independently via `BaseN.field` (read and write), matching the static
# dispatch model used for v2.1 unbound-self method calls.
from tpy import Int32


class Counter:
    value: Int32


class Tag:
    value: str


class Combined(Counter, Tag):
    def __init__(self, n: Int32, label: str) -> None:
        Counter.value = n  # tpyc: ok
        Tag.value = label  # tpyc: ok

    def summary(self) -> str:
        n = Counter.value
        label = Tag.value
        return label + "=" + str(n)


def main() -> None:
    c = Combined(42, "answer")
    print(c.summary())


main()
