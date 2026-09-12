# Regression: print() on a narrowed value-Optional BigInt field. The print
# path's BigInt branch fires from get_resolved_type (narrowed inner) before
# the OptionalType branch can intercept, so the BigInt formatter must see
# the storage->value boundary handled by gen_expr_deref. int32/str/bool/
# float fields are already covered by narrowed_field_print.
class Stats:
    total: int | None
    label: int | None

    def __init__(self, total: int | None, label: int | None) -> None:
        self.total = total
        self.label = label

    def show(self) -> None:
        if self.total is None:
            print("no total")
        else:
            print(self.total)
        if self.label is not None:
            print(self.label, self.total)


def main() -> None:
    Stats(42, 7).show()
    Stats(None, None).show()


main()
