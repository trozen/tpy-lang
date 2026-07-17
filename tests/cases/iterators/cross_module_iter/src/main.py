# Iterate a cross-module generic iterable WITHOUT importing its type -- only
# the factory is imported. The element type of Bag[Int32].__iter__() ->
# Iterator[T] must still bind T=Int32, which requires resolving Bag's
# RecordInfo by qname (it is absent from this module's local records dict).
# Regression guard: this raised "Cannot iterate over type Bag[Int32]" before
# the qname-first lookup fix.
from bag import make_bag


def main() -> None:
    b = make_bag([10, 20, 30])
    total = 0
    for v in b:
        total += v
    print(total)


main()
