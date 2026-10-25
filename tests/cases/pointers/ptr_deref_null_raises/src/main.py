# An explicit dereference of a null Ptr raises AttributeError, CPython's
# error for `None.__deref__()`, under both interpreters.
from typing import Iterator

from tpy import Ptr, deref, int32, readonly, take_ptr
from tpy.unsafe import unsafe_const_cast, unsafe_ptr_add


class Box:
    def __init__(self, p: Ptr[int32]) -> None:
        self.p = p

    def value(self) -> int32:
        # method: deref of a null field
        return self.p.__deref__()  # tpyc: ok


def gen(p: Ptr[int32]) -> Iterator[int32]:
    # generator: deref after a yield
    yield 1
    yield deref(p)  # tpyc: ok


def free_function(p: Ptr[int32]) -> None:
    try:
        v = deref(p)  # tpyc: ok
        print("free", v)
    except AttributeError as e:
        print("free", e)
    try:
        w = p.__deref__()  # tpyc: ok
        print("dunder", w)
    except AttributeError as e:
        print("dunder", e)


def readonly_pointer(cp: Ptr[readonly[int32]]) -> None:
    try:
        v = deref(cp)  # tpyc: ok
        print("readonly", v)
    except AttributeError as e:
        print("readonly", e)


def method(p: Ptr[int32]) -> None:
    b = Box(p)
    try:
        v = b.value()
        print("method", v)
    except AttributeError as e:
        print("method", e)


def generator(p: Ptr[int32]) -> None:
    out: list[int32] = []
    try:
        for v in gen(p):
            out.append(v)
    except AttributeError as e:
        print("generator", out, e)


def unsafe_null(cp: Ptr[readonly[int32]]) -> None:
    # The unsafe pointer operations pass a null pointer on without dereferencing it.
    q = unsafe_ptr_add(unsafe_const_cast(cp), 0)
    try:
        v = deref(q)
        print("unsafe", v)
    except AttributeError as e:
        print("unsafe", e)


def non_null(x: int32) -> None:
    p = take_ptr(x)
    print("non_null", deref(p))


def main() -> None:
    # Both spellings of the null pointer.
    p = Ptr[int32]()
    q: Ptr[int32] = None
    cp = Ptr[readonly[int32]]()
    free_function(p)
    free_function(q)
    readonly_pointer(cp)
    method(p)
    generator(p)
    unsafe_null(cp)
    non_null(7)


main()
