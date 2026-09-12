# A lambda inside a generator body captures names the resumable FRAME stores
# as members. A member is not a variable, so the capture NAMES it in an
# init-capture, and the mode is the sync one: an escaping (`Callable`) closure
# snapshots the member by value, a non-escaping (`Fn`) one binds a reference
# to it. Never a handle on the frame itself.
from typing import Callable, Iterator
from tpy import Fn, int32, Own, ValueType


class Pt(ValueType):
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


def apply(f: Callable[[int32], int32], v: int32) -> int32:
    return f(v)


def apply_fn(f: Fn[[int32], int32], v: int32) -> int32:
    return f(v)


def push(ys: list[int32], v: int32) -> int32:
    ys.append(v)
    return len(ys)


class Registry:
    cb: Callable[[int32], int32]

    def __init__(self) -> None:
        self.cb = lambda x: x

    def register(self, f: Callable[[int32], int32]) -> None:
        self.cb = f


class C:
    n: int32

    def __init__(self) -> None:
        self.n = 10

    # generator METHOD, single yield (simple-generator peephole): the wrapper
    # lambda holds the receiver as `this`, and the inner capture spells `this`
    # too (self renders `(*this)` in that context).
    def emit(self, k: int32) -> Iterator[int32]:
        for i in range(k):
            yield apply(lambda x: x + self.n, i)  # tpyc: ok


class D:
    n: int32

    def __init__(self) -> None:
        self.n = 10

    # generator METHOD, two yields (resumable frame): the receiver is the
    # frame's `__self` reference member, and the lambda copies that HANDLE --
    # so a field written between the two yields is visible to the second
    # lambda, exactly as a sync method's `this` capture behaves.
    def emit(self) -> Iterator[int32]:
        yield apply(lambda x: x + self.n, 1)  # tpyc: ok
        self.n = 100
        yield apply(lambda x: x + self.n, 2)  # tpyc: ok


# free generator, two yields (frame): the captured name is an ordinary frame
# member (a param), the shape that used to emit `[n]` for a struct member.
def two_yield(n: int32) -> Iterator[int32]:
    yield apply(lambda x: x + n, 1)  # tpyc: ok
    yield apply(lambda x: x + n, 2)  # tpyc: ok


# The by-value capture is what makes a lambda safe to hand to a callee that
# STORES it: `r.cb` outlives the generator frame, and the snapshot it holds
# still reads 10 after the generator is exhausted and gone.
def store(n: int32, r: Registry) -> Iterator[int32]:
    r.register(lambda x: x + n)  # tpyc: ok
    yield 1
    yield 2


# The frame capture is a snapshot, so a captured local reassigned after the
# capture point warns exactly as it does in a sync body -- and, like
# nested_def/escaping_value_capture_reassigned, the closure runs BEFORE the
# reassignment so TPy and CPython still agree on the output.
def cell() -> Iterator[int32]:
    step = 1
    f: Callable[[int32], int32] = lambda x: x + step  # tpyc: warning(/reassigned after the closure is created/)
    yield apply(f, 1)
    step = 100
    yield step


# free generator, two yields (frame), NON-ESCAPING slot: `xs` is a borrowed
# reference-type param, so its frame member is the caller's object and the
# capture binds a reference to it -- the mutation through the closure is
# visible on the caller's list, as it is in the sync twin
# (nested_def/container_params) and in CPython.
def ref_capture(xs: list[int32]) -> Iterator[int32]:
    yield apply_fn(lambda v: push(xs, v), 9)  # tpyc: ok
    yield apply_fn(lambda i: xs[i], 2)  # tpyc: ok


# free generator, two yields (frame): an `Own[T]` member is snapshotted by its
# PAYLOAD, so the wrapper is peeled before the copyable verdict -- a value-typed
# payload copies, the `__del__`-carrying one rejects
# (nested_def/error_frame_lambda_nocopy_snapshot).
def own_capture(p: Own[Pt]) -> Iterator[int32]:
    yield apply(lambda i: i + p.x, 1)  # tpyc: ok
    yield apply(lambda i: i + p.x, 2)  # tpyc: ok


def main() -> None:
    c = C()
    for v in c.emit(3):
        print("peephole", v)

    for v2 in D().emit():
        print("method", v2)

    for v3 in two_yield(10):
        print("free", v3)

    r = Registry()
    for v4 in store(10, r):
        print("store", v4)
    print("store cb", r.cb(1))

    for v5 in cell():
        print("cell", v5)

    src = [1, 2]
    for v6 in ref_capture(src):
        print("ref", v6)
    print("ref after", src)

    for v7 in own_capture(Pt(5)):
        print("own", v7)


main()
