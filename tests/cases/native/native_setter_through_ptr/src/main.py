# A @native property setter on a @native class called through a Ptr from a
# @native function and through a tplib Box deref.
__tpy__ = {"includes": ["native_types.hpp"]}
from tpy import int32, Ptr
from tpy.extern import native
from tplib.box import Box as HBox


@native("NBox")
class NBox:
    @property
    @native("get_v")
    def v(self) -> int32: ...

    @v.setter
    @native("set_v")
    def v(self, value: int32) -> None: ...


@native("open_box")
def open_box(k: int32) -> Ptr[NBox]: ...


@native("make_box")
def make_box() -> NBox: ...


def via_hbox(h: HBox[NBox]) -> None:
    # tplib Box deref onto the native setter
    h.v = 5  # tpyc: ok


def value() -> int32:
    print("order value")
    return 3


def store(p: Ptr[NBox]) -> None:
    try:
        # order: the receiver's None check runs before the right-hand side,
        # where CPython evaluates the right-hand side first and prints
        # "order value" (BUGS.md#subexpression-right-to-left-eval)
        p.v = value()  # tpyc: nullable(p)
    except AttributeError:
        print("order caught")


BOX = make_box()


def recv() -> NBox:
    print("recv_order recv")
    return BOX


def recv_value() -> int32:
    print("recv_order value")
    return 4


def recv_store() -> None:
    # recv_order: the receiver call runs before the right-hand side, where
    # CPython prints "value" before "recv"
    # (BUGS.md#subexpression-right-to-left-eval)
    recv().v = recv_value()  # tpyc: ok
    print("recv_order", BOX.v)


def main() -> None:
    p = open_box(1)
    q = open_box(2)
    # Ptr from a @native function: the native setter, past the None check
    p.v = 1  # tpyc: nullable(p)
    print("ptr", q.v)
    # p is proven non-null from here on
    p.v = 2  # tpyc: non_null(p)
    print("ptr_non_null", q.v)

    h = HBox(make_box())
    via_hbox(h)
    print("hbox", h.get().v)

    store(None)
    recv_store()


main()
