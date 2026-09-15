# Truthiness of an isinstance-narrowed union member that defines __bool__ or
# __len__: the mode comes from the NARROWED occurrence, so the extraction alias
# is dispatched (`::tpy::__bool__(__x)`) rather than streamed bare -- the
# member type has no operator bool, so keying the mode on the declared union
# would emit `if (__x)`, which does not compile.
from tpy import int32


class Flag:
    def __init__(self, on: bool) -> None:
        self.on = on

    def __bool__(self) -> bool:
        return self.on


class Bag:
    def __init__(self, n: int32) -> None:
        self.n = n

    def __len__(self) -> int32:
        return self.n


class Plain:
    def __init__(self, tag: str) -> None:
        self.tag = tag


# free function: the __bool__ member, both branches.
def bool_member(x: Flag | Plain) -> str:
    if isinstance(x, Flag):
        if x:  # tpyc: ok
            return "flag-true"
        return "flag-false"
    return "plain"


# free function: the __len__ member reads the same way.
def len_member(x: Bag | Plain) -> str:
    if isinstance(x, Bag):
        if x:  # tpyc: ok
            return "bag-nonempty"
        return "bag-empty"
    return "plain"


# free function: the narrowed member as a `while` head.
def drain(x: Bag | Plain) -> int32:
    steps = 0
    if isinstance(x, Bag):
        while x:  # tpyc: ok
            steps += 1
            x.n -= 1
    return steps


class Reader:
    def __init__(self, tag: str) -> None:
        self.tag = tag

    # method: the same narrowed truthiness inside a record method body.
    def check(self, x: Flag | Plain) -> str:
        if isinstance(x, Flag):
            if x:  # tpyc: ok
                return self.tag + "-true"
            return self.tag + "-false"
        return self.tag + "-plain"


def main() -> None:
    print("bool", bool_member(Flag(True)), bool_member(Flag(False)),
          bool_member(Plain("p")))
    print("len", len_member(Bag(2)), len_member(Bag(0)),
          len_member(Plain("p")))
    print("while", drain(Bag(3)), drain(Plain("p")))
    print("method", Reader("r").check(Flag(True)),
          Reader("r").check(Flag(False)))


main()
