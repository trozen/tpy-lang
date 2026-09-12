# Property borrow tracking: property getter borrows via return_borrows_from
from tpy import int32

class Container:
    _items: list[int32]

    def __init__(self) -> None:
        self._items = [1, 2, 3]

    @property
    def items(self) -> list[int32]:
        return self._items

    @items.setter
    def items(self, v: list[int32]) -> None:
        self._items = v

def test_borrow_via_property() -> None:
    c = Container()
    v = c.items
    c._items = [4, 5, 6]  # tpyc: warning(/Mutation of 'c' while borrowed/)
    print(v)

def test_iter_via_property() -> None:
    c = Container()
    for x in c.items:
        c._items = [7, 8, 9]  # tpyc: warning(/Mutation of 'c' while borrowed/)
        print(x)
        break

def test_setter_invalidates_borrow() -> None:
    c = Container()
    v = c.items
    c.items = [10, 11, 12]  # tpyc: warning(/Mutation of 'c' while borrowed/)
    print(v)

def test_value_type_no_warn() -> None:
    c = Container()
    v = c.items[0]  # tpyc: ok
    c._items = [99]  # tpyc: ok
    print(v)

test_borrow_via_property()
test_iter_via_property()
test_setter_invalidates_borrow()
test_value_type_no_warn()
