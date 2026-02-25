# elif with a temp-producing condition (list literal passed by mutable ref).
# The temp declaration must be scoped correctly -- not between } and else.
from tpy import Int32

def has_items(items: list[Int32]) -> bool:
    return len(items) > 0

def test(x: Int32) -> Int32:
    if x < 0:
        return -1
    elif has_items([10, 20]):
        return 0
    else:
        return 1

def main() -> None:
    print(test(-5))
    print(test(0))
    print(test(5))

main()
