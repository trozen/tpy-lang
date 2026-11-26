# `@error_return` values a guard banks, each built in place where it is
# evaluated and reached on its own: guard taken, a raising call behind a
# false guard, and raising with the guard taken.
from tpy import int32, Own, error_return, ReturnException, nomove
from fixtures import P, tick


class E(Exception, ReturnException):
    pass


@nomove
class Pin:
    v: int32
    # A heap member: a slot engaged or destroyed wrongly frees garbage.
    tag: str

    def __init__(self, v: int32) -> None:
        print("mk Pin", v)
        self.v = v
        self.tag = "pin-heap-tag-long-string-0123456789" * (v + 2)


@error_return(E)
def er_str(n: int32) -> str:
    print("er_str", n)
    if n < 0:
        raise E()
    return "v" * (n * 20)


@error_return(E)
def er_big(n: int32) -> int:
    print("er_big", n)
    if n < 0:
        raise E()
    big: int = 10 ** 30
    return big + n


@error_return(E)
def er_int(n: int32) -> int32:
    print("er_int", n)
    if n < 0:
        raise E()
    return n


@error_return(E)
def er_q(n: int32) -> int32:
    # Prints nothing: its order beside another call is not under test.
    if n < 0:
        raise E()
    return n


@error_return(E)
def er_p(n: int32) -> Own[P]:
    print("er_p", n)
    if n < 0:
        raise E()
    return P(n)


def one() -> int32:
    return 1


def sum_pins(*ps: Pin) -> int32:
    s = 0
    for p in ps:
        s += p.v
    return s


def count(*bs: bool) -> int32:
    n = 0
    for b in bs:
        if b:
            n += 1
    return n


@error_return(E)
def run(k: int32, flag: bool, n: int32) -> int32:
    # Each check on its own branch, so each raises on its own run.
    if k == 0:
        # A guarded `str` held operand (an empty value takes the fallback).
        s_r = (er_str(n) or "fallback") if flag else "skip"  # tpyc: ok
        print("checks-str", s_r)
    elif k == 1:
        # A value pack, one checked element and two.
        big_r = tick(er_big(n)) if flag else 0  # tpyc: ok
        print("checks-big", big_r)
    elif k == 2:
        # The second element raises with the first built (`n` 2).
        big2_r = tick(er_big(n), er_big(n - 3)) if flag else 0  # tpyc: ok
        print("checks-big2", big2_r)
    elif k == 3:
        # A `@nomove` pack element.
        pin_r = flag and sum_pins(Pin(er_int(n))) > 0  # tpyc: ok
        print("checks-pin", pin_r)
    elif k == 4:
        # The check under an operator, and beside another call.
        op_r = flag and sum_pins(Pin(er_int(n) + 1)) > 0  # tpyc: ok
        print("checks-op", op_r)
    elif k == 5:
        call_r = flag and sum_pins(Pin(er_q(n) + one())) > 0  # tpyc: ok
        print("checks-call", call_r)
    elif k == 6:
        # Two checked elements.
        two_r = flag and sum_pins(Pin(er_q(n)), Pin(er_q(n + 1))) > 0  # tpyc: ok
        print("checks-two", two_r)
    elif k == 7:
        # A check in a ternary arm (taken for any nonzero `n`).
        arm_r = flag and sum_pins(Pin(er_q(n) if n != 0 else 3)) > 0  # tpyc: ok
        print("checks-arm", arm_r)
    elif k == 8:
        # The first element built, the second raising: the early return
        # leaves with one slot engaged.
        half_r = flag and sum_pins(Pin(er_q(n)), Pin(er_q(-1))) > 0  # tpyc: ok
        print("checks-half", half_r)
    else:
        # A checked chain root as a pack element in a ternary arm.
        chain_r = count(0 < er_p(n).n < 10) if flag else 0  # tpyc: ok
        print("checks-chain", chain_r)
    return k


def main() -> None:
    for flag, n in [(True, 2), (False, -1), (True, -1), (True, 5)]:
        for k in range(10):
            try:
                r = run(k, flag, n)
                print("checks", r, flag, n)
            except E:
                print("checks raised", k, flag, n)
    # `n` 0: an empty string takes the fallback, the ternary arm its `else`.
    for k in [0, 7]:
        try:
            r0 = run(k, True, 0)
            print("checks", r0, True, 0)
        except E:
            print("checks raised", k, True, 0)
