# A method filling a @dynamic protocol slot may write the parameters the slot
# declares writable, whatever its own body does: a call of it dispatches
# through the slot, so callers keep those arguments mutable.
from typing import Iterator, Protocol
from tpy import dynamic, readonly, Ptr
from tplib import Box


class Ray:
    d: float

    def __init__(self, d: float) -> None:
        self.d = d


class Tilted(Protocol):
    def tilt(self, ray: Ray) -> float: ...


@dynamic
class Surface(Tilted, Protocol):
    def bounce(self, ray: Ray) -> float: ...

    def count(self, rs: list[Ray]) -> int: ...

    def maybe(self, ray: Ray | None) -> float: ...

    def peek(self, ray: readonly[Ray]) -> float: ...


class Mat(Surface):
    k: float

    def __init__(self, k: float) -> None:
        self.k = k

    # None of these write their argument; the slot says they may.
    def bounce(self, ray: Ray) -> float:
        return ray.d * self.k

    def count(self, rs: list[Ray]) -> int:
        return len(rs)

    def maybe(self, ray: Ray | None) -> float:
        if ray is None:
            return 0.0
        return ray.d

    def peek(self, ray: readonly[Ray]) -> float:
        return ray.d

    def tilt(self, ray: Ray) -> float:
        return ray.d + 1.0


class Chrome(Mat):
    # The override writes through the parameter the slot declares writable.
    def bounce(self, ray: Ray) -> float:
        ray.d = -ray.d
        return ray.d

    def count(self, rs: list[Ray]) -> int:
        rs.append(Ray(9.0))
        return len(rs)


class Plain:
    def __init__(self) -> None:
        pass

    def bounce(self, ray: Ray) -> float:
        return ray.d


def trace(m: Mat, ray: Ray) -> float:
    return m.bounce(ray)  # tpyc: ok


def trace_box(m: Box[Mat], ray: Ray) -> float:
    return m.bounce(ray)  # tpyc: ok


def trace_ptr(m: Ptr[Mat], ray: Ray) -> float:
    return m.bounce(ray)  # tpyc: ok


def count_of(m: Mat, rs: list[Ray]) -> int:
    return m.count(rs)  # tpyc: ok


def maybe_of(m: Mat, ray: Ray | None) -> float:
    return m.maybe(ray)  # tpyc: ok


def tilt_of(m: Mat, ray: Ray) -> float:
    # the slot is declared by the @dynamic protocol's plain parent protocol
    return m.tilt(ray)  # tpyc: ok


def peek_of(m: Mat, ray: Ray) -> float:
    # inverse: a readonly[...] slot parameter keeps the caller's const
    return m.peek(ray)  # tpyc: ok


def plain_of(p: Plain, ray: Ray) -> float:
    # inverse: a method filling no slot keeps its body's verdict
    return p.bounce(ray)  # tpyc: ok


class Tracer:
    ray: Ray

    def __init__(self) -> None:
        self.ray = Ray(4.0)

    def run(self, m: Mat) -> float:
        # method body, the argument a field
        return m.bounce(self.ray)  # tpyc: ok


def gen(m: Mat, ray: Ray) -> Iterator[float]:
    # generator body
    yield m.bounce(ray)  # tpyc: ok


def main() -> None:
    r = Ray(3.0)
    print("free", trace(Mat(2.0), r), r.d)
    # a subclass override writes; the caller's object shows it
    print("override", trace(Chrome(1.0), r), r.d)
    b: Box[Mat] = Box(Chrome(1.0))
    print("box", trace_box(b, r), r.d)
    c = Chrome(1.0)
    print("ptr", trace_ptr(c, r), r.d)
    rs = [Ray(1.0)]
    print("list", count_of(Mat(1.0), rs), count_of(Chrome(1.0), rs), len(rs))
    print("optional", maybe_of(Mat(1.0), r), maybe_of(Mat(1.0), None))
    print("parent_protocol", tilt_of(Mat(1.0), r))
    print("readonly_slot", peek_of(Mat(1.0), r))
    print("plain", plain_of(Plain(), r))
    t = Tracer()
    # a named local: a temporary here is sliced (BUGS.md#method-temp-subclass-arg-sliced)
    ch = Chrome(1.0)
    print("method", t.run(ch), t.ray.d)
    for v in gen(Chrome(1.0), r):
        print("generator", v, r.d)
    # closure body, the argument an element
    rays = [Ray(5.0), Ray(6.0)]

    def inner() -> float:
        return trace(Chrome(1.0), rays[0])  # tpyc: ok

    print("closure", inner(), rays[0].d)
    # comprehension body
    print("comp", [trace(Chrome(1.0), x) for x in rays], rays[1].d)


main()
