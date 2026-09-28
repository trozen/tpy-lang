# A resumable frame's cleanup on an escaping exception runs innermost first,
# one ordered list: an inner `finally` before the release of the loop source
# around it (the release runs the source generator's own `finally`). A
# cleanup that raises or returns replaces the in-flight exception, and the
# enclosing handlers, `__exit__`s and finallies still run on the new path;
# a finally's exception is never caught by its own try's handlers.
# And a frame that finishes keeps a local a yielded reference may point into
# (the guard the finished-frame release of BUGS.md#finished-frame-keeps-locals
# must keep passing).
import asyncio
from typing import Iterator

from tpy import int32


def src(tag: str) -> Iterator[int32]:
    try:
        yield 1
        yield 2
    finally:
        print(tag, "source finally")


# --- generator ---

def gen_unwind() -> Iterator[int32]:
    try:
        for v in src("gen"):
            try:
                raise ValueError("v")
            except ValueError:
                yield v
                raise KeyError("k")
            finally:
                # The subject: runs before the loop source is released, as
                # the exception leaves the generator.
                print("gen inner finally", v)
    finally:
        print("gen outer finally")


class Ctx:
    def __enter__(self) -> "Ctx":
        return self

    def __exit__(self, et, ev, tb) -> None:
        print("with exit")


def with_unwind() -> Iterator[int32]:
    with Ctx():
        try:
            yield 1
            raise ValueError("v")
        except ValueError:
            yield 2
            raise KeyError("k")
        finally:
            # The subject: a `with` around the handler runs it, then exits.
            print("with inner finally")


def once_unwind() -> Iterator[int32]:
    try:
        try:
            yield 1
            raise ValueError("v")
        except ValueError:
            yield 2
        finally:
            # The subject: runs once, although the later throw leaves the
            # same resume state.
            print("once inner finally")
        raise KeyError("k")
    except KeyError:
        print("once caught")


# --- generator method ---

class Walker:
    tag: str

    def __init__(self, tag: str) -> None:
        self.tag = tag

    def walk(self) -> Iterator[int32]:
        try:
            for v in src(self.tag):
                try:
                    raise ValueError("v")
                except ValueError:
                    yield v
                    raise KeyError("k")
                finally:
                    # The method twin of gen_unwind.
                    print(self.tag, "inner finally", v)
        except KeyError:
            print(self.tag, "caught")


# --- async def ---

async def async_unwind() -> None:
    try:
        for v in src("async"):
            try:
                raise ValueError("v")
            except ValueError:
                await asyncio.sleep(0)
                raise KeyError("k")
            finally:
                # The coroutine twin: the suspension is an await.
                print("async inner finally", v)
    except KeyError:
        print("async caught")


# --- a cleanup that raises or returns while unwinding ---

class Seen:
    tag: str

    def __init__(self, tag: str) -> None:
        self.tag = tag

    def __enter__(self) -> "Seen":
        return self

    def __exit__(self, t: None, v: BaseException | None, tb: None) -> None:
        print(self.tag, "exit none", v is None, "index", isinstance(v, IndexError))


def fin_raises_caught() -> Iterator[int32]:
    try:
        try:
            yield 1
            raise ValueError("v")
        except ValueError:
            yield 2
            raise KeyError("k")
        finally:
            print("caught inner finally")
            # The subject: the new IndexError is matched by the handlers.
            raise IndexError("i")
    except KeyError:
        print("caught K")
    except IndexError:
        print("caught I")


async def async_fin_raises_caught() -> None:
    try:
        try:
            await asyncio.sleep(0)
            raise ValueError("v")
        except ValueError:
            await asyncio.sleep(0)
            raise KeyError("k")
        finally:
            print("async-caught inner finally")
            # The subject: the coroutine twin of fin_raises_caught.
            raise IndexError("i")
    except KeyError:
        print("async-caught K")
    except IndexError:
        print("async-caught I")


def fin_raises_in_with() -> Iterator[int32]:
    with Seen("in-with"):
        try:
            yield 1
            raise ValueError("v")
        except ValueError:
            yield 2
            raise KeyError("k")
        finally:
            print("in-with inner finally")
            # The subject: `__exit__` runs and sees the IndexError.
            raise IndexError("i")


def fin_raises_outer_finally() -> Iterator[int32]:
    try:
        try:
            yield 1
            raise ValueError("v")
        except ValueError:
            yield 2
            raise KeyError("k")
        finally:
            print("outer-fin inner finally")
            # The subject: no handler matches; the outer finally still runs.
            raise IndexError("i")
    except KeyError:
        print("outer-fin caught K")
    finally:
        print("outer-fin outer finally")


def fin_raises_catch_all() -> Iterator[int32]:
    try:
        for v in src("catch-all"):
            try:
                raise ValueError("v")
            except ValueError:
                yield v
                raise KeyError("k")
            finally:
                print("catch-all inner finally")
                # The subject: the loop source is released, then the
                # IndexError is caught.
                raise IndexError("i")
    except IndexError:
        print("catch-all caught I")
    print("catch-all end")


def fin_returns_outer_finally() -> Iterator[int32]:
    try:
        try:
            yield 1
            raise KeyError("k")
        except KeyError:
            yield 2
            raise ValueError("v")
        finally:
            print("ret inner finally")
            # The subject: cancels the ValueError; the outer handler does
            # not run, the outer finally does.
            return
    except ValueError:
        print("ret outer handler")
    finally:
        print("ret outer finally")


def fin_returns_in_with() -> Iterator[int32]:
    with Seen("ret-with"):
        try:
            yield 1
            raise KeyError("k")
        except KeyError:
            yield 2
            raise ValueError("v")
        finally:
            print("ret-with inner finally")
            # The subject: `__exit__` sees no exception.
            return


def handler_finally_unguarded() -> Iterator[int32]:
    try:
        raise ValueError("v")
    except ValueError:
        yield 1
        raise KeyError("k")
    finally:
        # The subject: the only try is the one whose handler raises.
        print("outermost finally")


# --- a finally's own exception is never its own try's to catch ---

def own_handler_normal() -> Iterator[int32]:
    try:
        try:
            yield 1
        except IndexError:
            print("own-normal handler BAD")
        finally:
            print("own-normal fin")
            # The subject: raised on the normal exit; only the outer
            # handler catches it, and this finally runs once.
            raise IndexError("i")
    except IndexError:
        print("own-normal outer caught I")


async def async_own_handler_normal() -> None:
    try:
        try:
            await asyncio.sleep(0)
        except IndexError:
            print("async-own-normal handler BAD")
        finally:
            print("async-own-normal fin")
            # The subject: the coroutine twin of own_handler_normal.
            raise IndexError("i")
    except IndexError:
        print("async-own-normal outer caught I")


def own_handler_unwind() -> Iterator[int32]:
    try:
        try:
            try:
                yield 1
                raise KeyError("k")
            except KeyError:
                yield 2
                raise ValueError("v")
            finally:
                print("own-unwind inner fin")
                return
        except IndexError:
            print("own-unwind mid handler BAD")
        finally:
            print("own-unwind mid fin")
            # The subject: raised on the inner finally's return path;
            # the mid try's own handler does not catch it.
            raise IndexError("i")
    except IndexError:
        print("own-unwind outer caught I")


# --- a raise after a `return` in a finally cancels the return ---

def stale_unwind() -> Iterator[int32]:
    try:
        try:
            try:
                yield 1
                raise KeyError("k")
            except KeyError:
                yield 2
                raise ValueError("v")
            finally:
                print("stale-unwind inner fin")
                return
        finally:
            print("stale-unwind mid fin")
            # The subject: the caught IndexError cancels the return, so
            # the frame goes on to `yield 3`.
            raise IndexError("i")
    except IndexError:
        print("stale-unwind outer caught I")
    yield 3
    print("stale-unwind end")


def stale_normal() -> Iterator[int32]:
    try:
        try:
            try:
                yield 1
            finally:
                print("stale-normal inner fin")
                return
        finally:
            print("stale-normal mid fin")
            # The subject: the normal-exit twin of stale_unwind.
            raise IndexError("i")
    except IndexError:
        print("stale-normal outer caught I")
    yield 3
    print("stale-normal end")


class Boom:
    tag: str

    def __init__(self, tag: str) -> None:
        self.tag = tag

    def __enter__(self) -> "Boom":
        return self

    def __exit__(self, t: None, v: BaseException | None, tb: None) -> None:
        print(self.tag, "boom exit none", v is None)
        raise IndexError("i")


def stale_exit_raises() -> Iterator[int32]:
    try:
        with Boom("stale-exit"):
            try:
                yield 1
                raise KeyError("k")
            except KeyError:
                yield 2
                raise ValueError("v")
            finally:
                print("stale-exit inner fin")
                # The subject: `__exit__` raises on this return's path; the
                # caught IndexError cancels it and the frame goes on.
                return
    except IndexError:
        print("stale-exit caught I")
    print("stale-exit end")


# --- a finished frame and a yielded reference ---

DROPPED: list[int32] = []


class Noisy:
    tag: int32

    def __init__(self, tag: int32) -> None:
        self.tag = tag

    def __del__(self) -> None:
        DROPPED.append(self.tag)
        self.tag = -1


def noisy_items() -> Iterator[Noisy]:
    xs = [Noisy(1), Noisy(2)]
    for x in xs:
        yield x


def drain(g: Iterator[Noisy]) -> None:
    for _ in g:
        pass


def lent_after_finish() -> None:
    g = noisy_items()
    for n in g:
        drain(g)
        # The subject: `g` finished inside `drain`, and `n` still points
        # into its list, so the frame keeps that list until it is destroyed.
        print("lent", n.tag)


def show(tag: str, g: Iterator[int32]) -> None:
    # Drained before printing: the section's own lines come first.
    got: list[int32] = []
    for v in g:
        got.append(v)
    print(tag, "got", got)


def main() -> None:
    try:
        for v in gen_unwind():
            print("gen got", v)
    except KeyError:
        print("gen caught")
    try:
        for v in with_unwind():
            print("with got", v)
    except KeyError:
        print("with caught")
    for v in once_unwind():
        print("once got", v)
    for v in Walker("method").walk():
        print("method got", v)
    asyncio.run(async_unwind())
    for v in fin_raises_caught():
        print("caught got", v)
    asyncio.run(async_fin_raises_caught())
    try:
        for v in fin_raises_in_with():
            print("in-with got", v)
    except IndexError:
        print("in-with main caught I")
    try:
        for v in fin_raises_outer_finally():
            print("outer-fin got", v)
    except IndexError:
        print("outer-fin main caught I")
    for v in fin_raises_catch_all():
        print("catch-all got", v)
    for v in fin_returns_outer_finally():
        print("ret got", v)
    print("ret done")
    for v in fin_returns_in_with():
        print("ret-with got", v)
    print("ret-with done")
    try:
        for v in handler_finally_unguarded():
            print("outermost got", v)
    except KeyError:
        print("outermost caught K")
    show("own-normal", own_handler_normal())
    asyncio.run(async_own_handler_normal())
    show("own-unwind", own_handler_unwind())
    show("stale-unwind", stale_unwind())
    show("stale-normal", stale_normal())
    show("stale-exit", stale_exit_raises())
    lent_after_finish()
    print("dropped", sorted(DROPPED))


main()
