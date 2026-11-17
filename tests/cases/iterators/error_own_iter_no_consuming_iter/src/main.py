# docs/LANGUAGE_FEATURES.md "Auto-Consuming Iteration at Last Use": own_iter()
# takes only a value whose type defines a consuming __iter__ (self:
# Own[Self]); a class whose only __iter__ lends has none.
from typing import Iterator
from tpy import int32, own_iter


class Bag:
    items: list[int32]

    def __init__(self) -> None:
        self.items = [1]

    def __iter__(self) -> Iterator[int32]:
        return iter(self.items)


def test() -> None:
    b = Bag()
    # The subject: the refusal names the type without a consuming __iter__.
    for k in own_iter(b):  # tpyc: error(/own_iter\(\) takes a value whose type defines a consuming __iter__ \(self: Own\[Self\]\); 'Bag' has none/)
        print(k)
