# Own[T] param consumption check: warns when param is only borrowed, not consumed.
# Consuming = store in field, forward to Own[T] param, or return as Own[T].
from tpy import Int32, Own, copy, ValueType

class Box:
    value: Int32
    def __init__(self, value: Int32) -> None:
        self.value = value

class Holder:
    item: Box
    def __init__(self, item: Own[Box]) -> None:
        self.item = item  # consumed: stored in field

# Warning: reads but never consumes
def borrow_only(b: Own[Box]) -> Int32:  # tpyc: warning(/never consumed/)
    return b.value

# No warning: forwards to another Own[T] param at last use
def forward(b: Own[Box]) -> Int32:
    return borrow_only(b)

# No warning: returns as Own[T]
def passthrough(b: Own[Box]) -> Own[Box]:
    return b

# No warning: stores via copy()
def copy_store(b: Own[Box]) -> Int32:
    h = Holder(copy(b))
    return h.item.value

# No warning: ValueType bound -- copy == move, no semantic difference
def value_type_borrow[T: ValueType](x: Own[T]) -> T:  # tpyc: ok
    return x

def main() -> None:
    print(borrow_only(Box(1)))
    print(forward(Box(2)))
    print(passthrough(Box(3)).value)
    print(copy_store(Box(4)))
    print(Holder(Box(5)).item.value)

main()
