# Generic async free function called with T inferred to a non-value
# (reference-typed) class. Exercises the `_CoroParamKind.TYPE_PARAM`
# reference-semantics path: at instantiation, `val_or_ref_t<T>` resolves
# to `T&` and `param_val_or_ref_t<T>` resolves to `T&`. To prove the
# storage really is a reference (not a copy), the async function mutates
# x through a protocol method and the caller observes the mutation on its
# binding after the await. Under copy semantics, c.n would still be 10.
# Distinct from `generic_async_free_func_nocopy`, which passes `Box[T]`
# (a composed shape that lands in the `REF` kind, not `TYPE_PARAM`).
import asyncio
from typing import Protocol
from tpy import Int32


class Bumpable(Protocol):
    def bump(self) -> None: ...


class Counter:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n

    def bump(self) -> None:
        self.n = self.n + 1


async def increment[T: Bumpable](x: T) -> Int32:
    x.bump()
    return Int32(0)


async def main_coro() -> None:
    c = Counter(Int32(10))
    _ = await increment(c)  # tpyc: type(Int32)
    print(c.n)  # 11 if reference (correct), 10 if copy


def main() -> None:
    asyncio.run(main_coro())


main()
