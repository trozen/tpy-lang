# Builtin functions, constructors and builtin-type methods take keyword
# arguments as CPython 3.12 does: each call binds through the stub's
# parameters (`/` and `*` markers, CPython's names), as a user function's.
import asyncio
from tpy import String, int32


def pick[T](a: T, b: T) -> T:
    return a


def ident[T](x: T) -> T:
    return x


def free_function() -> None:
    xs = [3, 1, 2]
    print("fn round", round(2.567, ndigits=2))  # tpyc: ok
    for i, v in enumerate(xs, start=1):  # tpyc: ok
        print("fn enumerate", i, v)
    print("fn sum", sum(xs, start=5))  # tpyc: ok
    print("fn sorted", sorted(xs, key=lambda v: -v, reverse=True))  # tpyc: ok
    print("fn sorted reverse", sorted(xs, reverse=True))  # tpyc: ok
    print("fn str", str(object=5))  # tpyc: ok
    # String takes str's parameter name.
    print("fn String", String(object=5))  # tpyc: ok
    print("fn bytes", len(bytes(source=3)))  # tpyc: ok
    print("fn min", min(xs, key=lambda v: -v))  # tpyc: ok
    # A lambda by keyword, its call the argument of a generic call (analyzed
    # again there): the binding is the same each time.
    print("fn ident max", ident(max(xs, key=lambda v: -v)))  # tpyc: ok
    print("fn pick sum", pick(sum(xs, start=5), 0))  # tpyc: ok


class Holder:
    text: str
    xs: list[int32]
    d: dict[str, int32]

    def __init__(self) -> None:
        self.text = "a,b,c"
        self.xs = [5, 7, 6]
        self.d = {"a": 1}

    def method(self) -> None:
        print("method split", self.text.split(sep=","))  # tpyc: ok
        print("method split max", self.text.split(sep=",", maxsplit=1))  # tpyc: ok
        self.xs.sort()
        print("method sorted", sorted(self.xs, key=lambda v: -v))  # tpyc: ok
        # dict.get's parameters are positional-only.
        print("method get", self.d.get("a", 0))  # tpyc: ok
        print("method get missing", self.d.get("z", 0))


async def async_body() -> None:
    await asyncio.sleep(0)
    xs = [4, 9, 2]
    print("async round", round(3.14159, ndigits=3))  # tpyc: ok
    print("async max", max(xs, key=lambda v: v % 5))  # tpyc: ok
    print("async enumerate", [i for i, _ in enumerate(xs, start=10)])  # tpyc: ok


free_function()
Holder().method()
asyncio.run(async_body())

# Module level: the same calls at global scope.
ys = [8, 6, 7]
print("module sum", sum(ys, start=1))  # tpyc: ok
print("module sorted", sorted(ys, reverse=True))  # tpyc: ok
print("module str", str(object=1.5))  # tpyc: ok
