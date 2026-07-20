# Truthiness of a pointer-repr Optional[record] narrowed past None must
# dispatch the record's __bool__/__len__ (regression: it emitted a bare
# non-null pointer test, always true once narrowed). Covers the not / while /
# and siblings that recurse into the same truthy leaf, plus a plain record
# (no dunder -> always truthy, matching CPython's object default).


class Flag:
    def __init__(self, on: bool):
        self.on = on

    def __bool__(self) -> bool:
        return self.on


class Bag:
    def __init__(self, n: int):
        self.n = n

    def __len__(self) -> int:
        return self.n


class Plain:
    def __init__(self, v: int):
        self.v = v


def bool_if(c: Flag | None) -> int:
    if c is None:
        return -1
    if c:  # narrowed to Flag -> Flag.__bool__
        return 1
    return 0


def bool_not(c: Flag | None) -> int:
    if c is None:
        return -1
    if not c:
        return 10
    return 11


def bool_while(c: Flag | None) -> int:
    if c is None:
        return -1
    n = 0
    while c:
        n += 1
        break
    return n


def bool_and(c: Flag | None, d: Flag | None) -> int:
    if c is None or d is None:
        return -1
    if c and d:
        return 20
    return 21


def len_if(b: Bag | None) -> int:
    if b is None:
        return -1
    if b:  # narrowed to Bag -> Bag.__len__
        return 1
    return 0


def plain_if(p: Plain | None) -> int:
    if p is None:
        return -1
    if p:  # plain record -> always truthy
        return 1
    return 0


def main():
    print(bool_if(None), bool_if(Flag(False)), bool_if(Flag(True)))
    print(bool_not(Flag(False)), bool_not(Flag(True)))
    print(bool_while(Flag(False)), bool_while(Flag(True)))
    print(bool_and(Flag(False), Flag(True)), bool_and(Flag(True), Flag(True)))
    print(len_if(Bag(0)), len_if(Bag(3)))
    print(plain_if(Plain(0)))


main()
