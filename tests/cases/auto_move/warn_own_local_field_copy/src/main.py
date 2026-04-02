# Own[T] rvalue-init local stored in field: warn at non-last-use, silent at last-use.
from tpy import Int32

class Item:
    value: Int32
    def __init__(self, v: Int32) -> None:
        self.value = v

class Holder:
    item: Item
    def __init__(self) -> None:
        self.item = Item(Int32(0))

def test_non_last_use() -> None:
    x = Item(Int32(1))
    h = Holder()
    h.item = x  # tpyc: warning(/copies Item into field/)
    print(x.value)

def test_last_use() -> None:
    x = Item(Int32(2))
    h = Holder()
    h.item = x  # tpyc: ok (last use -- auto-moved)

def test_container_non_last_use() -> None:
    x = Item(Int32(3))
    items: list[Item] = []
    items.append(x)  # tpyc: warning(/copies Item into owned storage/)
    print(x.value)

def test_container_last_use() -> None:
    x = Item(Int32(4))
    items: list[Item] = []
    items.append(x)  # tpyc: ok (last use)

test_non_last_use()
test_last_use()
test_container_non_last_use()
test_container_last_use()
