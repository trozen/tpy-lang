# The borrow-alias off a method-call receiver is admitted only when the call
# hands back a MUTABLE reference: a `@readonly` receiver method returns
# `const Mid&`, and the decl's const verdict does not travel through the field
# hop, so the alias would be spelled `Jar&` over `const Jar`.
# See BUGS.md#const-borrow-call-field-lift-loses-const. Workaround: bind the
# receiver first (`m = h.peek_ro()` then `j = m.jar`).
from tpy import int32, readonly


class Jar:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class Mid:
    jar: Jar

    def __init__(self, t: int32) -> None:
        self.jar = Jar(t)


class H:
    mid: Mid

    def __init__(self, t: int32) -> None:
        self.mid = Mid(t)

    @readonly
    def peek_ro(self) -> Mid:
        return self.mid


def read(h: H) -> int32:
    j = h.peek_ro().jar  # tpyc: error(/not yet supported/)
    return j.x


def main() -> None:
    print(read(H(5)))


main()
