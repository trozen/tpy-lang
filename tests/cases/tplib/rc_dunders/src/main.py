# Rc dunders: content equality, ordering, hashing, and string conversion.
# Mirrors Box's dunder coverage; verifies the Equatable/Comparable/Hashable
# bounds dispatch correctly through Rc to the underlying T.
from tpy import Int32
from tplib import Rc, make_rc


def main() -> None:
    a = make_rc(Int32(10))
    b = make_rc(Int32(10))   # same content, different cell
    c = make_rc(Int32(20))

    # Content equality (not identity): a and b are distinct cells but equal content.
    print(a == b)             # True
    print(a == c)             # False
    print(a != c)             # True

    # Ordering.
    print(a < c)              # True
    print(c < a)              # False
    print(a <= b)             # True
    print(a >= b)             # True
    print(c > a)              # True

    # Hash equality follows content equality.
    print(hash(a) == hash(b)) # True

    # __str__ / __repr__.
    print(str(a))             # Rc(10)
    print(repr(c))            # Rc(20)


main()
