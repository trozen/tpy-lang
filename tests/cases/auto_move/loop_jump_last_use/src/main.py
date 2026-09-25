# A consume that a `break`, a `continue`, a zero-trip loop or a `return` /
# `raise` through a `finally` reaches a later read from is not a last use: it
# copies (warned), so the read sees the value.
# The copies are the documented Own[T] divergence; `take` does not mutate what
# it receives, so the output matches CPython, and a moved-from P prints 0.
import asyncio
from typing import Iterator
from tpy import int32, Own


class P:
    xs: list[int32]

    def __init__(self, x: int32) -> None:
        self.xs = [x]


class Sink:
    ps: list[P]
    ls: list[list[P]]

    def __init__(self) -> None:
        self.ps = []
        self.ls = []


SINK = Sink()


class Guard:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def __enter__(self) -> int32:
        return self.n

    def __exit__(self, et, ev, tb) -> None:
        self.n += 1


class Suppress:
    def __init__(self) -> None:
        pass

    def __enter__(self) -> None:
        pass

    def __exit__(self, et, ev, tb) -> bool:
        return True


class Res:
    n: list[int32]

    def __init__(self, n: int32) -> None:
        self.n = [n]

    def __enter__(self) -> "Res":
        return self

    def __exit__(self, et, ev, tb) -> None:
        self.n[0] = -1


def fail_at(i: int32, at: int32) -> None:
    if i == at:
        raise ValueError("fail")


def take(p: Own[P]) -> int32:
    SINK.ps.append(p)
    return len(SINK.ps)


def take_list(ps: Own[list[P]]) -> int32:
    n = len(ps)
    SINK.ls.append(ps)
    return n


def first(ps: list[P]) -> P:
    return ps[0]


def for_break() -> None:
    p = P(1)
    for i in range(3):
        if i == 1:
            take(p)  # tpyc: warning(/copies P into owned storage/)
            break
    print("for_break", len(p.xs))


def while_break() -> None:
    p = P(1)
    i = 0
    while i < 3:
        i += 1
        if i == 2:
            take(p)  # tpyc: warning(/copies P into owned storage/)
            break
    print("while_break", len(p.xs))


def while_true_break() -> None:
    p = P(1)
    i = 0
    while True:
        i += 1
        if i == 2:
            take(p)  # tpyc: warning(/copies P into owned storage/)
            break
    print("while_true_break", len(p.xs))


def match_break() -> None:
    p = P(1)
    for i in range(3):
        match i:
            case 1:
                take(p)  # tpyc: warning(/copies P into owned storage/)
                break
            case _:
                pass
    print("match_break", len(p.xs))


def continue_rebind() -> None:
    p = P(1)
    for i in range(3):
        print("continue_rebind", i, len(p.xs))
        if i == 0:
            take(p)  # tpyc: warning(/copies P into owned storage/)
            continue
        p = P(5)


def finally_break() -> None:
    p = P(1)
    n = 0
    for i in range(3):
        try:
            if i == 1:
                take(p)  # tpyc: warning(/copies P into owned storage/)
                break
        finally:
            n += 1
    print("finally_break", n, len(p.xs))


def with_break() -> None:
    p = P(1)
    g = Guard()
    for i in range(3):
        with g:
            if i == 1:
                take(p)  # tpyc: warning(/copies P into owned storage/)
                break
    print("with_break", g.n, len(p.xs))


def try_else_break() -> None:
    p = P(1)
    for i in range(3):
        try:
            if i < 2:
                raise ValueError("retry")
        except ValueError:
            print("try_else_break", i, len(p.xs))
        else:
            break
        # The handler reads p again on the next iteration.
        take(p)  # tpyc: warning(/copies P into owned storage/)


def except_break() -> None:
    p = P(1)
    for i in range(3):
        try:
            fail_at(i, 1)
        except ValueError:
            take(p)  # tpyc: warning(/copies P into owned storage/)
            break
    print("except_break", len(p.xs))


def handler_break_finally() -> None:
    p = P(1)
    for i in range(3):
        try:
            fail_at(i, 1)
        except ValueError:
            # The finally runs between the break and the loop exit, and reads p.
            take(p)  # tpyc: warning(/copies P into owned storage/)
            break
        finally:
            print("handler_break_finally", i, len(p.xs))


def handler_continue_finally() -> None:
    p = P(1)
    for i in range(3):
        try:
            fail_at(i, 1)
        except ValueError:
            take(p)  # tpyc: warning(/copies P into owned storage/)
            continue
        finally:
            print("handler_continue_finally", i, len(p.xs))


def with_target_finally() -> None:
    t = Res(0)
    for i in range(3):
        try:
            # The finally reads t after the break, so the manager outlives the with.
            with Res(5) as t:
                fail_at(i, 0)
            break
        except ValueError:
            pass
        finally:
            print("with_target_finally", i, t.n[0])


def return_finally() -> int32:
    p = P(1)
    try:
        fail_at(0, 0)
    except ValueError:
        # The finally runs after the return and reads p.
        take(p)  # tpyc: warning(/copies P into owned storage/)
        return 1
    finally:
        print("return_finally", len(p.xs))
    return 0


def raise_finally() -> None:
    p = P(1)
    try:
        fail_at(0, 0)
    except ValueError:
        take(p)  # tpyc: warning(/copies P into owned storage/)
        raise
    finally:
        print("raise_finally", len(p.xs))


def assert_finally(c: bool) -> None:
    p = P(1)
    try:
        fail_at(0, 0)
    except ValueError:
        if c:
            # The failing assert leaves through the finally too.
            take(p)  # tpyc: warning(/copies P into owned storage/)
            assert False, "boom"
    finally:
        print("assert_finally", len(p.xs))


def nested_return_finally() -> int32:
    p = P(1)
    try:
        try:
            fail_at(0, 0)
        except ValueError:
            # The return runs both finallies; the outer one reads p.
            take(p)  # tpyc: warning(/copies P into owned storage/)
            return 1
        finally:
            print("nested_return_finally inner")
    finally:
        print("nested_return_finally", len(p.xs))
    return 0


def deferred_return_finally() -> Own[P]:
    p = P(1)
    try:
        fail_at(0, 0)
    except ValueError:
        # A returned name still moves: it is materialized after the finally.
        return p  # tpyc: ok
    finally:
        print("deferred_return_finally", len(p.xs))
    return P(0)


def with_target_exception_finally() -> None:
    t = Res(0)
    try:
        # The exception leaves through the finally, which reads t.
        with Res(6) as t:
            fail_at(0, 0)
    finally:
        print("with_target_exception_finally", t.n[0])


def finally_continue() -> None:
    p = P(1)
    n = 0
    for i in range(3):
        print("finally_continue", i, len(p.xs))
        try:
            if i == 0:
                take(p)  # tpyc: warning(/copies P into owned storage/)
                continue
            p = P(5)
        finally:
            n += 1


def inner_else_break() -> None:
    p = P(1)
    for j in range(2):
        for i in range(0):
            pass
        else:
            # This break leaves the outer loop.
            take(p)  # tpyc: warning(/copies P into owned storage/)
            break
    print("inner_else_break", len(p.xs))


def suppressed_while_true() -> None:
    p = P(1)
    i = 0
    with Suppress():
        while True:
            p = P(2)
            # The exception fail_at raises leaves the loop, and the with swallows it.
            take(p)  # tpyc: warning(/copies P into owned storage/)
            fail_at(i, 1)
            i += 1
    print("suppressed_while_true", len(p.xs))


def with_target_break() -> None:
    t = Res(0)
    while True:
        # t is read after the loop the break leaves, so the manager outlives the with.
        with Res(5) as t:
            break
    print("with_target_break", t.n[0])


def nested_break() -> None:
    p = P(1)
    for j in range(2):
        print("nested_break", j, len(p.xs))
        for i in range(2):
            if j == 0:
                take(p)  # tpyc: warning(/copies P into owned storage/)
                break


def zero_trip_for(xs: list[int32]) -> None:
    y = P(1)
    take(y)  # tpyc: warning(/copies P into owned storage/)
    for x in xs:
        y = P(2)
    print("zero_trip_for", len(y.xs))


def zero_trip_while(n: int32) -> None:
    y = P(1)
    take(y)  # tpyc: warning(/copies P into owned storage/)
    i = 0
    while i < n:
        y = P(2)
        i += 1
    print("zero_trip_while", len(y.xs))


def while_true_rebind() -> None:
    y = P(1)
    # `while True:` never exits through its head, so y is rebound before any read.
    take(y)  # tpyc: ok
    while True:
        y = P(2)
        break
    print("while_true_rebind", len(y.xs))


def gen_break() -> Iterator[int32]:
    p = P(1)
    for i in range(3):
        yield i
        if i == 1:
            take(p)  # tpyc: warning(/copies P into owned storage/)
            break
    yield len(p.xs)


async def async_break() -> int32:
    p = P(1)
    for i in range(3):
        await asyncio.sleep(0)
        if i == 1:
            take(p)  # tpyc: warning(/copies P into owned storage/)
            break
    return len(p.xs)


class Runner:
    n: int32

    def __init__(self) -> None:
        p = P(1)
        for i in range(3):
            if i == 1:
                take(p)  # tpyc: warning(/copies P into owned storage/)
                break
        self.n = len(p.xs)

    def run(self) -> None:
        p = P(1)
        for i in range(3):
            if i == 1:
                take(p)  # tpyc: warning(/copies P into owned storage/)
                break
        print("method_break", len(p.xs), self.n)


def iterable_consumed() -> None:
    xs = [P(1), P(2)]
    for x in xs:
        # The loop still iterates xs, so its consume copies even on a path that leaves.
        n = take_list(xs)  # tpyc: warning(/copies list\[P\] into owned storage/)
        print("iterable_consumed", n, len(x.xs))
        break


def iterable_consumed_return() -> int32:
    xs = [P(1), P(2)]
    for x in xs:
        # The copy holds on a return path too: the loop still iterates xs there.
        return take_list(xs) * 10 + len(x.xs)  # tpyc: warning(/copies list\[P\] into owned storage/)
    return 0


def alias_iterable_consumed() -> None:
    xs = [P(1), P(2)]
    ys = xs
    for x in ys:
        # The loop iterates xs through its alias ys.
        n = take_list(xs)  # tpyc: warning(/copies list\[P\] into owned storage/)
        print("alias_iterable_consumed", n, len(x.xs))
        break


def alias_call_borrow() -> None:
    xs = [P(1), P(2)]
    ys = xs
    v = first(ys)
    # v borrows xs through its alias ys.
    n = take_list(xs)  # tpyc: warning(/copies list\[P\] into owned storage/)
    print("alias_call_borrow", n, len(v.xs))


def last_use_break() -> None:
    p = P(1)
    n = 0
    for i in range(3):
        if i == 1:
            # Nothing reads p after the loop: still a move.
            n = take(p)  # tpyc: ok
            break
    print("last_use_break", n)


def else_skipped_by_break() -> None:
    p = P(1)
    n = 0
    for i in range(3):
        if i == 1:
            # The `else` that reads p runs only when the loop does not break.
            n = take(p)  # tpyc: ok
            break
    else:
        n = len(p.xs)
    print("else_skipped_by_break", n)


def main() -> None:
    for_break()
    while_break()
    while_true_break()
    match_break()
    continue_rebind()
    finally_break()
    try_else_break()
    except_break()
    handler_break_finally()
    handler_continue_finally()
    with_target_finally()
    return_finally()
    try:
        raise_finally()
    except ValueError:
        print("raise_finally caught")
    try:
        assert_finally(True)
    except AssertionError:
        print("assert_finally caught")
    nested_return_finally()
    d = deferred_return_finally()
    print("deferred_return_finally", len(d.xs))
    try:
        with_target_exception_finally()
    except ValueError:
        print("with_target_exception_finally caught")
    finally_continue()
    inner_else_break()
    suppressed_while_true()
    with_target_break()
    with_break()
    nested_break()
    zero_trip_for([])
    zero_trip_while(0)
    while_true_rebind()
    print("gen_break", list(gen_break()))
    print("async_break", asyncio.run(async_break()))
    Runner().run()
    iterable_consumed()
    print("iterable_consumed_return", iterable_consumed_return())
    alias_iterable_consumed()
    alias_call_borrow()
    last_use_break()
    else_skipped_by_break()


main()
