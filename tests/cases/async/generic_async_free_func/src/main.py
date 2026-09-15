# Generic async free function: T is inferred from arg(s) at the await
# site. Two invariants exercised: (a) the callee's sub-coro struct
# carries `<T_substituted>` in the awaited frame slot, and (b) the
# awaited-value slot in the caller has the substituted type, not bare T.
# The `sized_after` section adds the readonly-generic frame slot, whose
# classifier is shared with the generator frame (iterators/gen_generic).
import asyncio
from typing import Protocol

from tpy import int32, readonly


class Readable(Protocol):
    @readonly
    def size(self) -> int32: ...


class Bin:
    total: int32

    def __init__(self, n: int32) -> None:
        self.total = n

    @readonly
    def size(self) -> int32:
        return self.total


async def identity[T](x: T) -> T:
    return x


async def sized_after[T: Readable](obj: readonly[T], alias: Bin) -> int32:
    # the `readonly[T]` slot in a CORO frame: the same shared classifier the
    # generator frame uses (`val_or_cref_t<T>` -- a const reference at a
    # reference instantiation, a copy at a value one), read after a suspend
    # so the slot has to survive the resume. `alias` is the SAME object as
    # `obj` at the one call below, mutated after the suspend: the read then
    # answers 15 through the const reference and would answer 5 if the slot
    # had copied, so this section is not parity-blind about the form.
    await asyncio.sleep(0)
    alias.total += 10
    return obj.size()


async def main_coro() -> None:
    result = await identity(int32(42))  # tpyc: type(int32)
    print(result)
    s = await identity("hi")  # tpyc: type(str)
    print(s)
    b = Bin(5)
    # the same object at both params, so the mutation lands under the slot
    print("readonly", await sized_after(b, b))  # tpyc: ok
    # the caller still owns the object the const slot referred to
    b.total += 1
    print("readonly after", b.total)


def main() -> None:
    asyncio.run(main_coro())


main()
