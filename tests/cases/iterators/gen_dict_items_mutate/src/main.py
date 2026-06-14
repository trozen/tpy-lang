# Generator iterating d.items() across yields: the unpacked value var
# aliases the dict's stored object (proxy-ref iterator + borrow-form
# tuple frame slot), so mutations inside the generator reach the dict.
# Covers both the unpack form and the whole-tuple kv form.
from tpy import Int32
from typing import Iterator


class C:
    v: Int32

    def __init__(self, v: Int32):
        self.v = v


def bump(d: dict[Int32, C]) -> Iterator[Int32]:
    for k, c in d.items():
        c.v = c.v + 1
        yield k


def pairs(d: dict[Int32, C]) -> Iterator[Int32]:
    for kv in d.items():
        kv[1].v = kv[1].v + 10
        yield kv[0]


def main():
    d = {1: C(10), 2: C(20)}
    for k in bump(d):
        print(k)
    print(d[1].v, d[2].v)
    for k in pairs(d):
        print(k)
    print(d[1].v, d[2].v)


main()
