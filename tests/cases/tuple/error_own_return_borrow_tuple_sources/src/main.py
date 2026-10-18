# A borrowed non-name tuple source returned into an owning tuple slot warns per
# copied element, then rejects (BUGS.md#borrow-tuple-source-own-return-rejects).
from tpy import Own, int32


class A:
    def __init__(self, x: int32) -> None:
        self.x = x


class H:
    p: tuple[A, int32]

    def __init__(self) -> None:
        self.p = (A(1), 2)

    def get(self, a: A) -> tuple[A, int32]:
        return (a, 2)

    # method: a field of self returned whole
    def own_pair(self) -> Own[tuple[A, int32]]:
        return self.p  # tpyc: warning(/copies A into owned storage \(tuple element 0\)/)


def mk(a: A) -> tuple[A, int32]:
    return (a, 1)


# free function: a borrow-returning call; the first body the lowering refuses
def from_call(a: A) -> Own[tuple[A, int32]]:
    return mk(a)  # tpyc: warning(/copies A into owned storage \(tuple element 0\)/) error(/call\.ret_type\.tuple/)


# free function: a method call
def from_method(h: H, a: A) -> Own[tuple[A, int32]]:
    return h.get(a)  # tpyc: warning(/copies A into owned storage \(tuple element 0\)/)


# free function: a field
def from_field(h: H) -> Own[tuple[A, int32]]:
    return h.p  # tpyc: warning(/copies A into owned storage \(tuple element 0\)/)


# free function: a container element
def from_subscript(xs: list[tuple[A, int32]]) -> Own[tuple[A, int32]]:
    return xs[0]  # tpyc: warning(/copies A into owned storage \(tuple element 0\)/)


# free function: a select of two borrow-returning calls
def from_select_calls(h: H, a: A, c: bool) -> Own[tuple[A, int32]]:
    return mk(a) if c else h.get(a)  # tpyc: warning(/copies A into owned storage \(tuple element 0\)/)


# free function: a select of a borrow-returning call and a tuple local
def from_select_name(a: A, c: bool) -> Own[tuple[A, int32]]:
    p = mk(a)
    return mk(a) if c else p  # tpyc: warning(/copies A into owned storage \(tuple element 0\)/)


# free function: an Own element slot fed by a borrow-returning call
def into_own_element(a: A) -> tuple[Own[A], int32]:
    return mk(a)  # tpyc: warning(/copies A into owned storage \(tuple element 0\)/)


def take(t: Own[tuple[A, int32]]) -> int32:
    b, n = t
    b.x = 9
    return b.x


def take_elem(t: tuple[Own[A], int32]) -> Own[A]:
    return t[0]


# The same select at the other owning sinks (each is refused by the lowering
# as the return is: BUGS.md#borrow-tuple-source-own-return-rejects).
# free function: a whole Own tuple argument
def select_at_own_arg(a: A, c: bool) -> int32:
    p = mk(a)
    return take(mk(a) if c else p)  # tpyc: warning(/copies A into owned storage \(tuple element 0\)/)


# free function: an Own element argument
def select_at_own_elem_arg(a: A, c: bool) -> int32:
    p = mk(a)
    return take_elem(mk(a) if c else p).x  # tpyc: warning(/copies A into owned storage \(tuple element 0\)/)


# free function: a container insert
def select_at_append(a: A, c: bool) -> int32:
    p = mk(a)
    xs: list[tuple[A, int32]] = []
    xs.append(mk(a) if c else p)  # tpyc: warning(/copies A into owned storage \(tuple element 0\)/)
    return xs[0][0].x


class SelectField:
    p: tuple[A, int32]

    # method: a field store
    def __init__(self, a: A, c: bool) -> None:
        p = mk(a)
        self.p = mk(a) if c else p  # tpyc: warning(/copies A into field \(tuple element 0\)/)


def nested_mixed(a: A) -> tuple[Own[tuple[A, int32]], A]:
    return ((A(2), 1), a)


def take_nested(t: tuple[Own[tuple[A, int32]], Own[A]]) -> int32:
    return t[1].x


# free function: a call whose nested element is wholly owned: only the
# borrowed element 1 copies
def nested_at_own_elem_arg(a: A) -> int32:
    return take_nested(nested_mixed(a))  # tpyc: warning(/copies A into owned storage \(tuple element 1\)/)


class NestedField:
    q: tuple[tuple[A, int32], A]

    # method: the same call into a field
    def __init__(self, a: A) -> None:
        self.q = nested_mixed(a)  # tpyc: warning(/copies A into field \(tuple element 1\)/)


def main() -> None:
    a = A(1)
    h = H()
    print(from_call(a)[0].x, from_method(h, a)[0].x, from_field(h)[0].x)
    xs: list[tuple[A, int32]] = []
    xs.append((A(2), 1))
    print(from_subscript(xs)[0].x)
    print(into_own_element(a)[0].x, h.own_pair()[0].x)
    print(from_select_calls(h, a, True)[0].x, from_select_name(a, False)[0].x)
    print(select_at_own_arg(a, True), select_at_own_elem_arg(a, False),
          select_at_append(a, True), SelectField(a, False).p[0].x)
    print(nested_at_own_elem_arg(a), NestedField(a).q[1].x)


main()
