# @readonly function returning non-value property from const param
from tpy import Int32, readonly

class Foo:
    _items: list[Int32]
    def __init__(self) -> None:
        self._items = [1, 2, 3]

    @property
    def items(self) -> list[Int32]:
        return self._items

@readonly
def get_count(f: Foo) -> Int32:
    return len(f.items)

def main() -> None:
    f = Foo()
    print(get_count(f))

main()
