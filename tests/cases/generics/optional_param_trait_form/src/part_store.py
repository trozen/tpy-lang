# The constructor's store of an open-T `T | None` parameter (bare, Send- or
# Sync-qualified) into its field, and an open-T `T | None` local bound to a
# call's result. The store copies a class payload (warned, as for the concrete
# twin), so those sections only read the stored value; the copy itself is
# pinned by optional_param_trait_form_copy. A `@nocopy` payload is refused at
# the store, as at the bare `T | None` field.
from tpy import Send, Sync, ValueType, int32

from fixtures import C, P, V


class Holder[T]:
    _v: T | None

    def __init__(self, v: T | None) -> None:
        self._v = v  # tpyc: warning(/may copy T \| None into field/)

    def has(self) -> bool:
        return self._v is not None


class SendHolder[T]:
    # A qualified Optional field is the same storage slot as `T | None`'s, so
    # the qualified parameter stores into it as the bare one does.
    _v: Send[T] | None

    def __init__(self, v: Send[T] | None) -> None:
        self._v = v  # tpyc: warning(/may copy Send\[T\] \| None into field/)

    def show(self) -> None:
        print("send_store", self._v)
        # A narrowed read of the qualified field.
        if self._v is not None:
            print("send_store_narrowed", self._v)  # tpyc: ok


class SyncHolder[T]:
    # The unbounded Sync-qualified field stores its parameter alike.
    _v: Sync[T] | None

    def __init__(self, v: Sync[T] | None) -> None:
        self._v = v  # tpyc: warning(/may copy Sync\[T\] \| None into field/)

    def show(self) -> None:
        print("sync_store_unbounded", self._v)


class SyncValueHolder[T: ValueType]:
    _v: Sync[T] | None

    def __init__(self, v: Sync[T] | None) -> None:
        self._v = v  # tpyc: ok

    def show(self) -> None:
        print("sync_store", self._v)
        if self._v is not None:
            print("sync_store_narrowed", self._v)


def first[T](xs: list[T]) -> T | None:
    if len(xs) == 0:
        return None
    return xs[0]


def local[T](xs: list[T]) -> None:
    y = first(xs)
    print("local", y is not None, y)  # tpyc: ok


def bounded[U: ValueType](xs: list[U]) -> None:
    # A ValueType-bounded caller reads the callee's result, printed directly
    # and through a local. That result is committed to the pointer form even
    # at a value `U` -- a known defect, not the intended form
    # (BUGS.md#generic-optional-return-committed-to-pointer).
    print("bounded", first(xs) is not None, first(xs))  # tpyc: ok
    y = first(xs)  # tpyc: ok
    print("bounded_local", y is not None, y)


def main() -> None:
    print("ctor", Holder(1).has(), Holder(V(2)).has(), Holder("s").has(),
          Holder(C(3)).has(), Holder[int32](None).has())
    no: int32 | None = None
    # The holders' Send / Sync verdicts follow the payload, as for `T | None`.
    sh = SendHolder(1)  # tpyc: is_send(yes) is_sync(yes)
    sh.show()
    SendHolder(V(2)).show()
    SendHolder("s").show()
    SendHolder(C(3)).show()
    SendHolder(no).show()
    sy = SyncHolder(1)  # tpyc: is_send(yes) is_sync(yes)
    sy.show()
    SyncHolder(V(2)).show()
    SyncHolder("s").show()
    SyncHolder(C(4)).show()
    SyncHolder(no).show()
    svh = SyncValueHolder(1)  # tpyc: is_send(yes) is_sync(yes)
    svh.show()
    SyncValueHolder(V(2)).show()
    SyncValueHolder(no).show()
    local([1])
    local([V(2)])
    local(["s"])
    ps = [P(5)]
    local(ps)
    local(list[int32]())
    big: int = 123456789012345678901234567890
    bounded([3])
    bounded([V(4)])
    bounded(["s"])
    bounded([big])
    bounded([(1, 2)])
    bounded(list[int32]())
