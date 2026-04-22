# Tuple form isinstance(x, (A, B)) expands to "A or B" narrowing,
# composing with and/or chains, negation, and match-case guards.
class A:
    tag: int
    def __init__(self, tag: int) -> None:
        self.tag = tag

class B:
    tag: int
    def __init__(self, tag: int) -> None:
        self.tag = tag

class C:
    z: int
    def __init__(self, z: int) -> None:
        self.z = z


def basic(v: A | B | C) -> str:
    if isinstance(v, (A, B)):
        return "ab"
    return "c"


def negate(v: A | B | C) -> int:
    if not isinstance(v, (A, B)):
        return v.z
    return -1


def and_rhs(v: A | B | C, flag: bool) -> str:
    if isinstance(v, (A, B)) and flag:
        return "ab-flag"
    return "other"


def or_lhs(v: A | B | C, flag: bool) -> str:
    if isinstance(v, (A, B)) or flag:
        return "maybe"
    return "c-no-flag"


def single_member_tuple(v: A | B | C) -> str:
    # Tuple form with one element: same as the non-tuple form.
    if isinstance(v, (A,)):
        return "a"
    return "not-a"


def match_guard(v: A | B | C) -> str:
    match v:
        case _ if isinstance(v, (A, B)):
            return "ab"
        case _:
            return "c"


def main() -> None:
    print(basic(A(1)))
    print(basic(B(2)))
    print(basic(C(3)))

    print(negate(A(1)))
    print(negate(C(30)))

    print(and_rhs(A(1), True))
    print(and_rhs(B(2), False))
    print(and_rhs(C(3), True))

    print(or_lhs(A(1), False))
    print(or_lhs(C(3), True))
    print(or_lhs(C(3), False))

    print(single_member_tuple(A(1)))
    print(single_member_tuple(B(2)))

    print(match_guard(A(1)))
    print(match_guard(B(2)))
    print(match_guard(C(3)))


main()
