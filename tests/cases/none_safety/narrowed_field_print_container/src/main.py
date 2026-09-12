# Regression: print() on a narrowed value-Optional container field. After
# `if self.f is not None:`, the field unwraps via gen_expr_deref's
# is_narrowed_optional_field branch so print sees the bare container and
# routes through ListPrinter/DictPrinter/SetPrinter -- NOT the Optional
# wrapper (which would also work, but is the wrong codegen shape).
from tpy import int32


class Bag:
    items: list[int] | None
    by_key: dict[str, int32] | None
    elems: set[int32] | None
    tup: tuple[int, int32] | None

    def __init__(
        self,
        items: list[int] | None,
        by_key: dict[str, int32] | None,
        elems: set[int32] | None,
        tup: tuple[int, int32] | None,
    ) -> None:
        self.items = items
        self.by_key = by_key
        self.elems = elems
        self.tup = tup

    def show(self) -> None:
        if self.items is not None:
            print(self.items)
        if self.by_key is not None:
            print(self.by_key)
        if self.elems is not None:
            print(self.elems)
        if self.tup is not None:
            print(self.tup)


def main() -> None:
    items: list[int] = [1, 2, 3]
    by_key: dict[str, int32] = {"a": 1}
    # Single-element set to avoid TPy vs CPython set-ordering differences.
    elems: set[int32] = {7}
    Bag(items, by_key, elems, (10, 20)).show()
    Bag(None, None, None, None).show()


main()
