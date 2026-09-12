# Arc dunders: content equality, ordering, hashing, and string conversion --
# the atomic sibling of rc_dunders. Verifies the Equatable/Comparable/Hashable
# bounds dispatch through Arc to the underlying T (Arc is @nocopy, so a silent
# copy would be a compile error rather than a hidden value duplication).
from tpy import int32
from tplib.arc import Arc


def main() -> None:
    a = Arc.new(int32(10))
    b = Arc.new(int32(10))   # same content, different cell
    c = Arc.new(int32(20))

    # Content equality (not identity): a and b are distinct cells, equal content.
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
    print(str(a))             # Arc(10)
    print(repr(c))            # Arc(20)


main()
