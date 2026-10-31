# A subclass value bound to a base-annotated local inside one arm of a block
# and read after it: the hoisted local holds the base part (`as_base`).
from typing import Iterator
from tpy import Own, copy, error_return, ReturnException, int32


class Base:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Der(Base):
    def __init__(self, n: int32) -> None:
        super().__init__(n)


# A base whose constructor takes the subclass: CPython never runs it on a
# binding, so a slice that builds the base from the subclass would show.
class IBase:
    n: int32

    def __init__(self, src: "IDer") -> None:
        self.n = src.n + 1000


class IDer(IBase):
    def __init__(self, n: int32) -> None:  # tpyc: warning(/does not call .super/)
        self.n = n


# A subclass whose copy runs user code: copy() calls it on the whole object.
class HDer(Base):
    def __init__(self, n: int32) -> None:
        super().__init__(n)

    def __copy__(self) -> Own["HDer"]:
        print("copy hook")
        return HDer(self.n + 100)


# The same hook on a generic subclass.
class GDer[T](Base):
    def __init__(self, n: int32, extra: T) -> None:
        super().__init__(n)
        self.extra = extra

    def __copy__(self) -> Own["GDer[T]"]:
        print("generic copy hook")
        return GDer(self.n + 100, copy(self.extra))


class Ctx:
    def __enter__(self) -> int32:
        return 1

    def __exit__(self, et, ev, tb) -> None:
        pass


class Fail(Exception, ReturnException):
    pass


def mk_ider(n: int32) -> Own[IDer]:
    return IDer(n)


# if, free function
def at_if(f: bool) -> None:
    if f:
        b: Base = Der(1)  # tpyc: warning(/upcast narrows/)
    else:
        return
    print("if", b.n)


# elif chain with one binding arm
def at_elif(k: int32) -> None:
    if k == 0:
        return
    elif k == 1:
        b: Base = Der(2)  # tpyc: warning(/upcast narrows/)
    else:
        return
    print("elif", b.n)


# try body, kept by an outer name too
def at_try(f: bool) -> None:
    keep = Base(0)
    try:
        b: Base = Der(3)  # tpyc: warning(/upcast narrows/)
        keep = b
    except ValueError:
        return
    print("try", b.n, keep.n)


# while body
def at_while() -> None:
    while True:
        b: Base = Der(4)  # tpyc: warning(/upcast narrows/)
        break
    print("while", b.n)


# for body
def at_for() -> None:
    for i in range(1):
        b: Base = Der(5 + i)  # tpyc: warning(/upcast narrows/)
    print("for", b.n)


# with body
def at_with() -> None:
    with Ctx() as k:
        b: Base = Der(5 + k)  # tpyc: warning(/upcast narrows/)
    print("with", b.n)


# match arm
def at_match(k: int32) -> None:
    match k:
        case 1:
            b: Base = Der(7)  # tpyc: warning(/upcast narrows/)
        case _:
            return
    print("match", b.n)


# method and constructor bodies
class Owner:
    m: int32

    def __init__(self, f: bool) -> None:
        self.m = 0
        if f:
            b: Base = Der(9)  # tpyc: warning(/upcast narrows/)
        else:
            return
        self.m = b.n

    def at_method(self, f: bool) -> int32:
        if f:
            b: Base = Der(8)  # tpyc: warning(/upcast narrows/)
        else:
            return 0
        return b.n


# closure: a nested def declares it
def at_closure(f: bool) -> None:
    def inner() -> int32:
        if f:
            b: Base = Der(10)  # tpyc: warning(/upcast narrows/)
        else:
            return 0
        return b.n
    print("closure", inner())


# @error_return body
@error_return(Fail)
def at_error_return(f: bool) -> int32:
    if f:
        b: Base = Der(11)  # tpyc: warning(/upcast narrows/)
    else:
        raise Fail
    return b.n


# a call result: the base part is moved out, IBase.__init__(IDer) never runs
def from_call(f: bool) -> None:
    if f:
        b: IBase = mk_ider(12)  # tpyc: warning(/upcast narrows/)
    else:
        return
    print("call", b.n)


# a subclass local at its last use moves its base part in
def from_moved(f: bool) -> None:
    d = IDer(13)
    if f:
        b: IBase = d  # tpyc: warning(/upcast narrows/)
    else:
        return
    print("moved", b.n)


# copy() of a subclass local copies its base part
def from_copy(f: bool) -> None:
    d = IDer(14)
    if f:
        b: IBase = copy(d)  # tpyc: warning(/upcast narrows/)
    else:
        return
    print("copy", b.n, d.n)


# a tuple element copied from a subclass local takes its base part from
# the local itself (`static_cast<const IBase&>(d)`), not from a copy
def tuple_elem_copy() -> None:
    d = IDer(20)
    t: tuple[IBase, int32] = (copy(d), 1)  # tpyc: warning(/upcast narrows/)
    d.n = 21
    print("tuple_copy", t[0].n, d.n)


# a copy() that runs a user __copy__ keeps the copy and takes the base part
# of its result, as a tuple element and as the hoisted local
def copy_hook(f: bool) -> None:
    d = HDer(22)
    t: tuple[Base, int32] = (copy(d), 1)  # tpyc: warning(/upcast narrows/)
    print("hook_tuple", t[0].n, d.n)
    if f:
        b: Base = copy(d)  # tpyc: warning(/upcast narrows/)
    else:
        return
    print("hook_hoist", b.n, d.n)


# the hook of a generic subclass runs too
def generic_copy_hook(f: bool) -> None:
    d = GDer(25, 7)
    if f:
        b: Base = copy(d)  # tpyc: warning(/upcast narrows/)
    else:
        return
    print("generic_hook", b.n, d.n)


# copy() of a subclass appended to a list of the base
def append_copy() -> None:
    d = IDer(23)
    bs: list[IBase] = []
    bs.append(copy(d))
    d.n = 24
    print("append_copy", bs[0].n, d.n)


# inverse: a subclass local used later binds by address, so a write through
# it after the binding shows through the base local
def alias_used_later(f: bool) -> None:
    d = Der(15)
    if f:
        b: Base = d  # tpyc: warning(/upcast narrows/)
    else:
        return
    d.n = 16
    print("alias", b.n, d.n)


# inverse: a base value of the local's own class
def same_class(f: bool) -> None:
    if f:
        b: Base = Base(17)  # tpyc: ok
    else:
        return
    print("same", b.n)


# inverse: both arms bind
def two_arm(f: bool) -> None:
    if f:
        b: Base = Der(18)  # tpyc: warning(/upcast narrows/)
    else:
        b = Der(19)  # tpyc: warning(/upcast narrows/)
    print("two_arm", b.n)


def main() -> None:
    at_if(True)
    at_elif(1)
    at_try(True)
    at_while()
    at_for()
    at_with()
    at_match(1)
    print("ctor", Owner(True).m)
    print("method", Owner(False).at_method(True))
    at_closure(True)
    try:
        print("error_return", at_error_return(True))
    except Fail:
        print("error_return failed")
    from_call(True)
    from_moved(True)
    from_copy(True)
    tuple_elem_copy()
    copy_hook(True)
    generic_copy_hook(True)
    append_copy()
    alias_used_later(True)
    same_class(True)
    two_arm(True)
    two_arm(False)


main()
