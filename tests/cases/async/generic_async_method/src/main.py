# Generic async method on a non-generic class: covers the method-side
# of the M7 invariant. Same propagation as generic free functions, but
# the operand is `obj.method(args)` rather than `f(args)` -- so the
# substituted FunctionInfo is reached via sema's method-call analysis
# and `_analyze_generic_method_call`. Pre-M7 this failed with
# "await operand must be a direct call to an async def ..." because
# `substitute_method_type_params` dropped `is_async` from the
# reconstructed FunctionInfo.
import asyncio
from tpy import Int32


class Container:
    def __init__(self, label: str):
        self.label = label

    async def echo[T](self, x: T) -> T:
        return x

    async def labeled[T](self, x: T) -> tuple[str, T]:
        return (self.label, x)


async def main_coro() -> None:
    c = Container("hi")
    a = await c.echo(Int32(7))  # tpyc: type(Int32)
    print(a)
    b = await c.echo("world")  # tpyc: type(str)
    print(b)
    p = await c.labeled(Int32(42))  # tpyc: type(/tuple\[str, Int32\]/)
    print(p[0])
    print(p[1])


def main() -> None:
    asyncio.run(main_coro())


main()
