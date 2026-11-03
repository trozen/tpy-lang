# A temporary held for a reference parameter is declared at its own object's
# type, so a subclass held for a base parameter is never sliced.
import asyncio
from typing import Iterator
from typing import Protocol
from tplib import Box
from tpy import Own
from tpy import copy
from tpy import dynamic
from tpy import readonly


@dynamic
class Surface(Protocol):
    @readonly
    def tag(self) -> str: ...

    def hit(self) -> str: ...


class Mat(Surface):
    hits: int

    def __init__(self) -> None:
        self.hits = 0

    @readonly
    def tag(self) -> str:
        return "mat"

    # The write is load-bearing: a parameter nothing writes is inferred
    # readonly and binds the temporary in place, so no storage is held.
    def hit(self) -> str:
        self.hits += 1
        return self.tag() + str(self.hits)


class Chrome(Mat):
    @readonly
    def tag(self) -> str:
        return "chrome"


class Other:
    def __init__(self) -> None:
        pass


# Every callee below writes its parameter through `hit`.
def use(m: Mat) -> str:
    return m.hit()


class Tracer:
    def __init__(self) -> None:
        pass

    def run(self, m: Mat) -> str:
        return use(m)

    def gen_m(self, m: Mat) -> Iterator[str]:
        yield use(m)

    def peek(self, m: Mat) -> str:
        return m.tag()

    def ro(self, m: readonly[Mat]) -> str:
        return m.tag()

    @staticmethod
    def st(m: Mat) -> str:
        return use(m)

    def __enter__(self) -> "Tracer":
        return self

    def __exit__(self, a: None, b: None, c: None) -> None:
        pass


class Holder:
    s: str

    def __init__(self, m: Mat) -> None:
        self.s = use(m)


class Pair:
    s: str

    def __init__(self, s: str, m: Mat) -> None:
        self.s = s + "+" + use(m)


class Reader[U: Surface]:
    def __init__(self) -> None:
        pass

    def run(self, m: U) -> str:
        return m.hit()



@dynamic
class Runner(Protocol):
    def run(self, m: Mat) -> str: ...


class DynTracer(Runner):
    def __init__(self) -> None:
        pass

    def run(self, m: Mat) -> str:
        return use(m)


class SRunner(Protocol):
    def run(self, m: Mat) -> str: ...


@dynamic
class Filler(Protocol):
    def fill(self, xs: list[int]) -> int: ...


class LFill(Filler):
    def __init__(self) -> None:
        pass

    def fill(self, xs: list[int]) -> int:
        xs.append(9)
        return len(xs)


def wrap(s: str) -> str:
    return "<" + s + ">"


def make() -> Own[Chrome]:
    return Chrome()


def gen(t: Tracer) -> Iterator[str]:
    # generator body
    yield t.run(Chrome())  # tpyc: ok
    # a generator factory's argument in a generator body: a frame field
    for s in t.gen_m(Chrome()):  # tpyc: ok
        yield "frame " + s


async def coro(t: Tracer) -> str:
    await asyncio.sleep(0)
    # async body
    return t.run(Chrome())  # tpyc: ok


def dyn_call(r: Runner) -> str:
    # a @dynamic protocol receiver's method: a temporary binds no `Mat&`
    return r.run(Chrome())  # tpyc: ok


def s_call(r: SRunner) -> str:
    # a structural protocol receiver's method
    return r.run(Chrome())  # tpyc: ok


def fill_call(f: Filler) -> int:
    # a list literal at a @dynamic protocol method's mutable list parameter
    return f.fill([1, 2])  # tpyc: ok


def opt_free(m: Mat | None) -> str:
    return "none" if m is None else use(m)


def union_free(m: Mat | Other) -> str:
    if isinstance(m, Mat):
        return use(m)
    return "other"


def proto_free(s: Surface) -> str:
    return s.tag()


def peek_free(m: Mat) -> str:
    return m.tag()


def box_free(b: Box[Mat]) -> str:
    return b.get().tag()


def main() -> None:
    t = Tracer()
    # method: the temporary held for `m` is a Chrome, never a Mat slice.
    print("method", t.run(Chrome()))  # tpyc: ok
    # keyword argument
    print("kwarg", t.run(m=Chrome()))  # tpyc: ok
    # constructor argument
    print("ctor", Holder(Chrome()).s)  # tpyc: ok
    # constructor argument beside a nested method call's argument
    print("nested_ctor", Pair(t.run(Chrome()), Chrome()).s)  # tpyc: ok
    # an argument of a call nested in another call
    print("nested_call", wrap(t.run(Chrome())))  # tpyc: ok
    print("gen", list(gen(t)))
    print("async", asyncio.run(coro(t)))
    # comprehension element
    print("comp", [t.run(Chrome()) for _ in range(2)])  # tpyc: ok
    with Tracer() as w:
        # `with` body
        print("with", w.run(Chrome()))  # tpyc: ok
    try:
        # `try` body
        print("try", t.run(Chrome()))  # tpyc: ok
    finally:
        pass
    # a call's own result, held at its own type
    print("call_result", t.run(make()))  # tpyc: ok
    # a generator method's factory: its frame keeps the Chrome
    print("gen_method", list(t.gen_m(Chrome())))  # tpyc: ok
    # a generic record's method at an instantiation with a base
    print("generic", Reader[Mat]().run(Chrome()))  # tpyc: ok
    print("dyn_receiver", dyn_call(DynTracer()))
    print("structural_receiver", s_call(Tracer()))
    print("list_literal", fill_call(LFill()))
    ch = Chrome()
    # an explicit copy is held at its own type; the original is untouched
    print("copy", t.run(copy(ch)), ch.hits)  # tpyc: ok
    # Unchanged: positions that held or bound the Chrome whole already.
    print("free", use(Chrome()))  # tpyc: ok
    print("static", Tracer.st(Chrome()))  # tpyc: ok
    print("optional", opt_free(Chrome()))  # tpyc: ok
    print("union", union_free(Chrome()))  # tpyc: ok
    print("protocol", proto_free(Chrome()))  # tpyc: ok
    # a readonly / inferred-const parameter binds the temporary in place
    print("readonly", t.ro(Chrome()))  # tpyc: ok
    print("const_method", t.peek(Chrome()))  # tpyc: ok
    # an inferred-const free function's parameter still holds it
    print("const_free", peek_free(Chrome()))  # tpyc: ok
    # a covariant generic record is held at the parameter's instantiation
    print("box", box_free(Box[Chrome](Chrome())))  # tpyc: ok


main()
# module level
print("module", Tracer().run(Chrome()))  # tpyc: ok
