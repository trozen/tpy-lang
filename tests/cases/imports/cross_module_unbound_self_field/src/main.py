# Regression: BaseN.field (multi-base unbound-self field access) works when
# BaseN is imported from another module. The IMPORTED_NAME branch in
# _try_unbound_self_field_access used to look up records via get_record(qname)
# (short-name keyed) which missed user-module records entirely.
from tpy import Int32
from bases import Counter, Tag


class Combined(Counter, Tag):
    def __init__(self, n: Int32, label: str) -> None:
        Counter.value = n  # tpyc: ok
        Tag.value = label  # tpyc: ok

    def summary(self) -> str:
        n = Counter.value  # tpyc: ok
        label = Tag.value  # tpyc: ok
        return label + "=" + str(n)


def main() -> None:
    c = Combined(Int32(42), "answer")
    print(c.summary())


main()
