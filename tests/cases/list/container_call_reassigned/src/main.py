# A local bound to a container-RETURNING call and later reassigned from another
# one: the two-slot rebind machinery, the container twin of the record shape.
# bytearray takes the same path as list and dict: it is one more reference
# type, not a family of its own.
from tpy import Own


def make(n: int) -> Own[list[int]]:
    out: list[int] = []
    for i in range(n):
        out.append(i)
    return out


def other(n: int) -> Own[list[int]]:
    out: list[int] = [n]
    return out


def counts() -> Own[dict[int, int]]:
    d: dict[int, int] = {1: 1}
    return d


def push(b: bytearray, v: int) -> None:
    b.append(v)


def main():
    # First bind fills `__slot_1`, the reseat fills the optional `__slot_2`;
    # the append after it proves the reseated local is the live container.
    r = make(3)  # tpyc: ok
    print(len(r))
    r = other(7)  # tpyc: ok
    r.append(9)
    print(len(r), r)
    d = counts()  # tpyc: ok
    d = counts()
    d[2] = 2
    print(len(d))
    ba = bytearray(b"ab")
    ba = bytearray(b"cd")  # tpyc: ok
    push(ba, 99)  # the param aliases the reseated buffer
    print(len(ba), bytes(ba))


main()
