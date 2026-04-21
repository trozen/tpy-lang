# or-chain isinstance: isinstance(v, A) or isinstance(v, B) must emit
# holds_alternative against the original variant, not an extracted ref.
class A:
    tag: int
    def __init__(self, tag: int) -> None:
        self.tag = tag

class B:
    tag: int
    def __init__(self, tag: int) -> None:
        self.tag = tag

class C:
    tag: int
    def __init__(self, tag: int) -> None:
        self.tag = tag


def two_member(v: A | B) -> str:
    if isinstance(v, A) or isinstance(v, B):
        return "hit"
    return "miss"


def three_member(v: A | B | C) -> str:
    if isinstance(v, A) or isinstance(v, B):
        return "ab"
    return "c"


def and_chain(v: A | B, flag: bool) -> str:
    if isinstance(v, A) and flag:
        return "a-flag"
    if isinstance(v, B) and flag:
        return "b-flag"
    return "none"


def negated_or(v: A | B) -> str:
    if not (isinstance(v, A) or isinstance(v, B)):
        return "impossible"
    return "reachable"


def main() -> None:
    print(two_member(A(1)))
    print(two_member(B(2)))

    print(three_member(A(1)))
    print(three_member(B(2)))
    print(three_member(C(3)))

    print(and_chain(A(1), True))
    print(and_chain(B(2), True))
    print(and_chain(A(1), False))

    print(negated_or(A(1)))


main()
