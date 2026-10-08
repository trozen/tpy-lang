# A `Ptr[T]` deref coercion into a `T` slot reads any source -- a field, a
# call result, an element -- not only a local name, at the return, argument
# and declaration positions. The pointee is aliased, never copied: every
# section writes through the result and prints the original object.
from tpy import Ptr, int32, readonly


class A:
    n: int32

    def __init__(self) -> None:
        self.n = 0


class M:
    _a: Ptr[A]

    def __init__(self, a: A) -> None:
        self._a = a

    def get(self) -> A:
        return self._a  # tpyc: ok

    def ro(self) -> readonly[A]:
        return self._a  # tpyc: ok

    @property
    def P(self) -> A:
        return self._a  # tpyc: ok

    def ptr(self) -> Ptr[A]:
        return self._a


def bump(x: A) -> None:
    x.n += 1


# return: a param's Ptr field, a call result, an element
def ret_field(m: M) -> A:
    return m._a  # tpyc: ok


def ret_call(m: M) -> A:
    return m.ptr()  # tpyc: ok


def ret_elem(ps: list[Ptr[A]]) -> A:
    return ps[0]  # tpyc: ok


# arg: the same three sources at an `A` param
def arg_sources(m: M, ps: list[Ptr[A]]) -> None:
    bump(m._a)  # tpyc: ok
    bump(m.ptr())  # tpyc: ok
    bump(ps[0])  # tpyc: ok


# arg_temp: a call source whose own argument needs a hoisted temporary
def ptr_with(a: A, scratch: list[int32]) -> Ptr[A]:
    scratch.append(1)
    return a


def arg_temp(a: A) -> None:
    bump(ptr_with(a, []))  # tpyc: ok


# decl: a field source binds the alias; `m` stays readonly because only the
# pointee is written
def decl_field(m: M) -> None:
    x: A = m._a  # tpyc: ok
    x.n += 1


# reseat: a reassigned alias re-points between two field sources
def reseat_field(m: M, m2: M, flag: bool) -> None:
    x: A = m._a
    if flag:
        x = m2._a  # tpyc: ok
    x.n += 1


def main() -> None:
    a = A()
    b = A()
    m = M(a)
    m2 = M(b)
    ps: list[Ptr[A]] = []
    ps.append(m.ptr())

    bump(m.get())
    print("method", a.n)
    print("readonly", m.ro().n)
    bump(m.P)
    print("property", a.n)
    bump(ret_field(m))
    print("ret_field", a.n)
    bump(ret_call(m))
    print("ret_call", a.n)
    bump(ret_elem(ps))
    print("ret_elem", a.n)
    arg_sources(m, ps)
    print("arg", a.n)
    arg_temp(a)
    print("arg_temp", a.n)
    decl_field(m)
    print("decl", a.n)
    reseat_field(m, m2, True)
    print("reseat", a.n, b.n)


main()
