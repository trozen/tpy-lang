# A borrow-returning call at an `Own[T]` return is a borrowed source whatever
# its receiver is: a TEMPORARY receiver does not exempt it, because what the
# callee hands back can reach past that receiver. All three receiver shapes
# below warn. The copy is the ACKNOWLEDGED CPython divergence (CPython hands
# back the very Point), so main prints only what both sides agree on and the
# WARNING is the pin; the `copy()` twin of each spelling is silent.
from tpy import int32, Own, copy


class Point:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x

    def updated(self) -> 'Point':
        self.x += 1
        return self


def make(n: int32) -> Own[Point]:
    # Constructor receiver: `Point(n)` is a temporary, and still a borrow.
    return Point(n).updated()  # tpyc: warning(/copies Point into owned storage/)


def make_copy(n: int32) -> Own[Point]:
    return copy(Point(n).updated())  # tpyc: ok


def owned(n: int32) -> Own[Point]:
    # Owning FREE-call receiver: `make(n)` hands back a value, and `updated`
    # still hands back a borrow of it.
    return make(n).updated()  # tpyc: warning(/copies Point into owned storage/)


def owned_copy(n: int32) -> Own[Point]:
    return copy(make(n).updated())  # tpyc: ok


class Factory:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def make(self) -> Own[Point]:
        return Point(self.n)


def owned_method(f: Factory) -> Own[Point]:
    # Owning METHOD receiver on a named parameter: the intermediate link is an
    # rvalue, which used to clear the chain and no longer does.
    return f.make().updated()  # tpyc: warning(/copies Point into owned storage/)


def owned_method_copy(f: Factory) -> Own[Point]:
    return copy(f.make().updated())  # tpyc: ok


def main() -> None:
    p = make(1)
    q = owned(1)
    r = owned_method(Factory(5))
    p.x = 10
    print(p.x, q.x, r.x)
    print(make_copy(1).x, owned_copy(1).x, owned_method_copy(Factory(5)).x)


main()
