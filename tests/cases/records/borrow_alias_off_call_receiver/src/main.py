# A local bound to a FIELD read off a borrow-returning METHOD CALL
# (`j = h.peek().jar`) binds the `T&` alias of that field, record and
# container fields alike. Every section mutates through the alias and reads
# the source back, so a silent copy would print the old value.
# The receiver call must hand back a C++ lvalue: a `-> Own[T]` receiver is a
# temporary and binds a copy instead (its own section below).
from tpy import Own, int32


class Jar:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class Mid:
    jar: Jar
    items: list[int32]

    def __init__(self, t: int32) -> None:
        self.jar = Jar(t)
        self.items = [t]


class H:
    mid: Mid

    def __init__(self, t: int32) -> None:
        self.mid = Mid(t)

    def peek(self) -> Mid:
        return self.mid

    def own_peek(self) -> Own[Mid]:
        return Mid(100)


# free function -- a RECORD field off the call receiver.
def free_record(h: H) -> int32:
    j = h.peek().jar  # tpyc: ok
    j.x = 42
    return h.mid.jar.x


# free function -- a CONTAINER field off the same receiver.
def free_container(h: H) -> int32:
    xs = h.peek().items  # tpyc: ok
    xs.append(7)
    return len(h.mid.items)


# free function -- an `Own`-returning receiver hands back a temporary, so the
# local is a COPY; mutating it leaves the source alone.
def free_own_receiver(h: H) -> int32:
    j = h.own_peek().jar  # tpyc: ok
    j.x = 5
    return h.mid.jar.x


class Reader:
    v: int32

    # constructor -- the same alias inside a ctor body.
    def __init__(self, h: H) -> None:
        j = h.peek().jar  # tpyc: ok
        j.x = 11
        self.v = h.mid.jar.x

    # method -- and inside a method body.
    def bump(self, h: H) -> int32:
        j = h.peek().jar  # tpyc: ok
        j.x += 1
        return h.mid.jar.x


def main() -> None:
    print("freerecord", free_record(H(1)))
    print("freecontainer", free_container(H(1)))
    print("freeown", free_own_receiver(H(1)))
    print("ctor", Reader(H(1)).v)
    h = H(3)
    r = Reader(h)
    print("method", r.bump(h))


main()
