# Generic `async def -> T` at an object-typed instantiation returns a
# borrow (val_or_ptr_t<T> Poll payload = T*): the awaited result ALIASES
# the argument, matching sync generics and CPython. A value-typed
# instantiation stays by value.
import asyncio


class Node:
    v: int

    def __init__(self, v: int) -> None:
        self.v = v


async def identity[T](x: T) -> T:
    await asyncio.sleep(0)
    return x


async def main() -> None:
    n = Node(1)
    r = await identity(n)
    r.v = 42
    print(n.v)
    s = await identity("plain")
    print(s)


asyncio.run(main())
