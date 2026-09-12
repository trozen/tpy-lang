# An @error_return success value MOVES (not copies) out of the dying expected
# into storage targets: a storage local, a field, and the auto-propagate local.
# @nocopy makes a reintroduced copy a hard build error.
from tpy import int32, error_return, ReturnException, Own, nocopy


class E(Exception, ReturnException):
    pass


@nocopy
class Payload:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


@error_return(E)
def make(n: int32) -> Own[Payload]:
    if n < 0:
        raise E
    return Payload(n)


@error_return(E)
def chain(n: int32) -> Own[Payload]:
    p = make(n)
    return p


class Sink:
    p: Payload

    def __init__(self) -> None:
        self.p = Payload(0)

    def fill(self, n: int32) -> None:
        try:
            self.p = make(n)
        except E:
            print("fill error")


def main() -> None:
    try:
        a = make(5)
    except E:
        print("error")
    else:
        print(a.v)

    try:
        b = chain(6)
    except E:
        print("error")
    else:
        print(b.v)

    s = Sink()
    s.fill(9)
    print(s.p.v)
    s.fill(-1)
    print(s.p.v)

    try:
        c = make(-1)
    except E:
        print("caught")


main()
