# A rebind slot is reserved at the declaration but its C++ declaration is held
# back until a rebind consumes it. These cover both sides of that:
#   - alias_survives_rebind / rebind_in_loop: the slot IS consumed, so it must
#     still be separate storage from the init slot -- an alias taken before the
#     rebind keeps pointing at the ORIGINAL value and observes mutation there.
#   - borrow_call_rebind / none_reassign: the reassignment needs no slot (a
#     borrow-returning call takes an address; None is a null pointer), so no
#     dead `std::optional<T>` may appear in the generated C++.
from tpy import int32


class Counter:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def bump(self) -> None:
        self.n += 1


def pick(c: Counter) -> Counter:
    return c


def alias_survives_rebind() -> None:
    c = Counter(1)
    alias = c
    c = Counter(10)  # tpyc: ok
    alias.bump()
    print("alias:", alias.n, "c:", c.n)


def rebind_in_loop() -> None:
    c = Counter(0)
    first = c
    i = 0
    while i < 3:
        c = Counter(i)  # tpyc: ok
        i += 1
    first.bump()
    print("first:", first.n, "c:", c.n)


def borrow_call_rebind(seed: Counter) -> None:
    result = seed
    i = 0
    while i < 2:
        result = pick(seed)
        i += 1
    result.bump()
    print("seed:", seed.n, "result:", result.n)


def none_reassign(flag: bool) -> None:
    c: Counter | None = Counter(5)
    if flag:
        c = None
    print("none:" if c is None else "some:", 0 if c is None else c.n)


def main() -> None:
    alias_survives_rebind()
    rebind_in_loop()
    borrow_call_rebind(Counter(7))
    none_reassign(True)
    none_reassign(False)


main()
