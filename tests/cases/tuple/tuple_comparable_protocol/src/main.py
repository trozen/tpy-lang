# tuple[T...] conforms to Comparable / Equatable when all element types do.
# C++ std::tuple provides lexicographic < and ==; the sema fix lets these
# operators route through generic-T: Comparable / T: Equatable bounds.
from tpy import Comparable, Equatable

def less[T: Comparable](a: T, b: T) -> bool:
    return a < b

def eq[T: Equatable](a: T, b: T) -> bool:
    return a == b

def main() -> None:
    a = (1, 2)
    b = (1, 3)
    c = (1, 2)

    print(less(a, b))
    print(less(b, a))
    print(eq(a, c))
    print(eq(a, b))

    # Mixed element types, still all-Comparable
    p = (1, "apple")
    q = (1, "banana")
    print(less(p, q))
    print(eq(p, p))

main()
