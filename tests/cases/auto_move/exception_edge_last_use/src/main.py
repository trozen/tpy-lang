# A consume an exception path reads (a finally, a handler, past a swallowing
# `with`) copies, warned; `take` does not mutate, so a moved-from P prints 0.
import asyncio
from typing import Iterator
from tpy import int32, Own, ReturnException, error_return


class P:
    xs: list[int32]

    def __init__(self, x: int32) -> None:
        self.xs = [x]


class Sink:
    ps: list[P]

    def __init__(self) -> None:
        self.ps = []


SINK = Sink()


def take(p: Own[P]) -> int32:
    SINK.ps.append(p)
    return len(SINK.ps)


def boom(c: bool) -> None:
    if c:
        raise ValueError("boom")


class Fail(Exception, ReturnException):
    pass


@error_return(Fail)
def check(c: bool) -> int32:
    if c:
        raise Fail()
    return 0


class Plain:
    def __init__(self) -> None:
        pass

    def __enter__(self) -> None:
        pass

    def __exit__(self, et, ev, tb) -> None:
        pass


class Swallow:
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


def handler_raise_finally() -> None:
    p = P(1)
    try:
        boom(True)
    except ValueError:
        # boom raises after the consume; the finally reads p.
        take(p)  # tpyc: warning(/copies P into owned storage/)
        boom(True)
        p = P(5)
    finally:
        print("handler_raise_finally", len(p.xs))


def outer_handler_reads() -> None:
    p = P(1)
    try:
        try:
            boom(True)
        except ValueError:
            # The inner handler's exception reaches the outer handler.
            take(p)  # tpyc: warning(/copies P into owned storage/)
            boom(True)
            p = P(5)
    except ValueError:
        print("outer_handler_reads", len(p.xs))


def swallowed_raise() -> None:
    p = P(1)
    with Swallow():
        # Swallow's __exit__ returns True: the code after the with runs.
        take(p)  # tpyc: warning(/copies P into owned storage/)
        raise ValueError("x")
    print("swallowed_raise", len(p.xs))


def swallowed_call() -> None:
    p = P(1)
    with Swallow():
        # boom's exception is swallowed; the rebind below never runs.
        take(p)  # tpyc: warning(/copies P into owned storage/)
        boom(True)
        p = P(5)
    print("swallowed_call", len(p.xs))


def gen_handler_raise_finally() -> Iterator[int32]:
    p = P(1)
    yield 0
    try:
        boom(True)
    except ValueError:
        # The same handler position in a generator frame.
        take(p)  # tpyc: warning(/copies P into owned storage/)
        boom(True)
        p = P(5)
    finally:
        print("gen_handler_raise_finally", len(p.xs))
    yield 1


async def async_handler_raise_finally() -> int32:
    p = P(1)
    try:
        await asyncio.sleep(0)
        boom(True)
    except ValueError:
        # The same handler position in a coroutine frame.
        take(p)  # tpyc: warning(/copies P into owned storage/)
        boom(True)
        p = P(5)
    finally:
        print("async_handler_raise_finally", len(p.xs))
    return 0


@error_return(Fail)
def error_return_finally() -> int32:
    p = P(1)
    try:
        # check's error propagates as a return, through the finally.
        take(p)  # tpyc: warning(/copies P into owned storage/)
        check(True)
        p = P(5)
    finally:
        print("error_return_finally", len(p.xs))
    return 0


def loop_handler_finally() -> None:
    p = P(1)
    for i in range(2):
        try:
            boom(True)
        except ValueError:
            # A handler in a loop body: the next iteration's finally reads p.
            take(p)  # tpyc: warning(/copies P into owned storage/)
            boom(i == 1)
        finally:
            print("loop_handler_finally", i, len(p.xs))


def if_with_swallow(c: bool) -> None:
    p = P(1)
    if c:
        # The raise ends the branch, but Swallow lets the code after the if run.
        with Swallow():
            take(p)  # tpyc: warning(/copies P into owned storage/)
            raise ValueError("x")
    print("if_with_swallow", len(p.xs))


def plain_with() -> None:
    p = P(1)
    with Plain():
        # Plain never swallows, but liveness runs before sema knows: copies.
        take(p)  # tpyc: warning(/copies P into owned storage/)
        p = P(5)
    print("plain_with", len(p.xs))


def handler_return_finally() -> Own[P]:
    p = P(1)
    try:
        boom(True)
    except ValueError:
        # The returned p is materialized after the finally consumed it.
        return p
    finally:
        take(p)  # tpyc: warning(/copies P into owned storage/)
    return P(0)


def with_target_handler() -> None:
    t = Res(0)
    try:
        # The handler reads t after the with body raises, so the manager
        # outlives the with.
        with Res(6) as t:
            boom(True)
        return
    except ValueError:
        print("with_target_handler", t.n[0])


def finally_consumes_return() -> Own[P]:
    p = P(1)
    try:
        # The returned p is materialized after the finally consumed it.
        return p
    finally:
        take(p)  # tpyc: warning(/copies P into owned storage/)


def match_arm_last_use(i: int32) -> None:
    p = P(1)
    n = 0
    match i:
        case 1:
            # An arm body ends the match; nothing after it reads p: still a move.
            n = take(p)  # tpyc: ok
        case _:
            n = len(p.xs)
    print("match_arm_last_use", n)


def return_or_consume_finally(early: bool) -> Own[P]:
    p = P(1)
    try:
        if early:
            return p
        # Only the return path reads p after the finally: still a move.
        take(p)  # tpyc: ok
    finally:
        print("return_or_consume_finally fin")
    return P(0)


def handler_last_use() -> None:
    p = P(1)
    try:
        boom(True)
    except ValueError:
        # Nothing on any path reads p afterwards: still a move.
        take(p)  # tpyc: ok
    print("handler_last_use", len(SINK.ps[-1].xs))


def try_body_last_use() -> None:
    p = P(1)
    try:
        # The handler does not read p: still a move.
        take(p)  # tpyc: ok
        boom(True)
    except ValueError:
        print("try_body_last_use caught")


def main() -> None:
    handler_raise_finally_caught = False
    try:
        handler_raise_finally()
    except ValueError:
        handler_raise_finally_caught = True
    print("handler_raise_finally caught", handler_raise_finally_caught)
    outer_handler_reads()
    swallowed_raise()
    swallowed_call()
    try:
        for v in gen_handler_raise_finally():
            print("gen_handler_raise_finally yield", v)
    except ValueError:
        print("gen_handler_raise_finally caught")
    try:
        asyncio.run(async_handler_raise_finally())
    except ValueError:
        print("async_handler_raise_finally caught")
    try:
        error_return_finally()
    except Fail:
        print("error_return_finally caught")
    try:
        loop_handler_finally()
    except ValueError:
        print("loop_handler_finally caught")
    if_with_swallow(True)
    plain_with()
    h = handler_return_finally()
    print("handler_return_finally", len(h.xs))
    with_target_handler()
    r = finally_consumes_return()
    print("finally_consumes_return", len(r.xs))
    match_arm_last_use(1)
    rc = return_or_consume_finally(True)
    print("return_or_consume_finally", len(rc.xs))
    return_or_consume_finally(False)
    handler_last_use()
    try_body_last_use()


main()
