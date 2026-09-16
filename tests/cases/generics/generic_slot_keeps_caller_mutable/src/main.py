# An open-`T` parameter renders `param_val_or_ref_t<T>`, a MUTABLE `T&` at
# every reference instantiation, so whatever a caller binds there must stay a
# mutable lvalue: a caller param that only feeds such a slot keeps its mutable
# borrow instead of being inferred `const T&`, which no `T&` slot could bind.
# The mutation verdict is what decides the caller's own spelling, so the
# generic slot has to count as a mutable use even though the generic body never
# writes. (`readonly[T]` is the declared opt-out, it renders
# `readonly_form_t<T>`; an argument at such a slot is a lowering reject today,
# `call.generic_arg_shape`, so there is no section for it.)
#
# A generic body cannot write through an open `T` yet (`assign.field_write_shape`
# rejects), so aliasing cannot be observed from the output. `NPt` is `@nocopy`
# instead: a silent copy at the parameter slot would be a compile error. The
# ctor sections use the copyable `Pt`, because a generic FIELD is an owning slot
# -- that copy is the subject of `generic_own_copy_verdict_instantiated`.
import asyncio
from typing import Iterator
from tpy import int32, nocopy


@nocopy
class NPt:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class Pt:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


# The subject: a generic body that only READS still needs a mutable borrow,
# because `param_val_or_ref_t<T>` has no const form.
def take[T](v: T) -> int32:
    return 1


class Holder[T]:
    item: T

    def __init__(self, v: T) -> None:
        self.item = v  # tpyc: warning(/may copy T into field/)


class Wrap(Holder[Pt]):
    def __init__(self, v: Pt) -> None:
        super().__init__(v)


# free function: the param binds the mutable open-T slot, so it stays `NPt&`
def via_free(p: NPt) -> int32:  # tpyc: ok
    return take(p)


class Driver:
    seen: int32

    def __init__(self) -> None:
        self.seen = 0

    # method
    def via_method(self, p: NPt) -> int32:  # tpyc: ok
        return take(p)

    # the RECEIVER at the slot: `self` binds the same mutable borrow, so this
    # method cannot be inferred const -- a `const Driver&` would not bind
    # `param_val_or_ref_t<Driver>` -- and its callers keep a mutable `Driver&`
    def via_self(self) -> int32:  # tpyc: ok
        me = self
        return take(me)


# constructor: a generic ctor slot is the same mutable borrow
def via_ctor(p: Pt) -> int32:  # tpyc: ok
    h = Holder(p)
    return h.item.x


# super(): the concrete subclass forwards into the generic base's ctor slot
def via_super(p: Pt) -> int32:  # tpyc: ok
    w = Wrap(p)
    return w.item.x


# generator body
def via_gen(p: NPt) -> Iterator[int32]:  # tpyc: ok
    yield take(p)


# async body
async def via_async(p: NPt) -> int32:  # tpyc: ok
    return take(p)


# comprehension
def via_comp(p: NPt) -> int32:  # tpyc: ok
    ys = [take(p) for _ in range(1)]
    return ys[0]


# try / finally
def via_finally(p: NPt) -> int32:  # tpyc: ok
    try:
        return take(p)
    finally:
        pass


# match arm
def via_match(p: NPt, tag: int32) -> int32:  # tpyc: ok
    match tag:
        case 1:
            return take(p)
        case _:
            return 0


async def main_coro() -> None:
    n = NPt(3)
    a = Pt(5)
    d = Driver()
    print("free", via_free(n))
    print("method", d.via_method(n), d.via_self())
    print("ctor", via_ctor(a), via_super(a))
    for v in via_gen(n):
        print("gen", v)
    print("async", await via_async(n))
    print("comp", via_comp(n))
    print("finally", via_finally(n))
    print("match", via_match(n, 1))


def main() -> None:
    asyncio.run(main_coro())


main()
