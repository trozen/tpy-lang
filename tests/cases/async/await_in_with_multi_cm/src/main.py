# `with` items around an `await` in an async def: two managers exit innermost
# first; item targets and comprehensions in a manager expression bind correctly.
import asyncio
from typing import Callable


class Tracer:
    def __init__(self, label: str) -> None:
        self.label = label

    def __enter__(self) -> None:
        print(f"enter {self.label}")

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        print(f"exit {self.label}")


async def value(n: int) -> int:
    return n


async def caller() -> int:
    with Tracer("A"), Tracer("B"):
        x = await value(7)
    return x


class Count:
    def __init__(self, v: int) -> None:
        self.v = v

    def __enter__(self) -> int:
        return self.v

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


class Cb:
    def __init__(self, f: Callable[[], int]) -> None:
        self.f = f

    def __enter__(self) -> int:
        return self.f() + 100

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


class Items:
    items: list[int]

    def __init__(self, v: int) -> None:
        self.items = [v]

    def __enter__(self) -> list[int]:
        return self.items

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


async def later_item_lambda() -> int:
    # A later item's manager is a lambda reading an earlier item's target.
    with Count(7) as n, Cb(lambda: n) as result:  # tpyc: ok
        await value(0)
        return n * 1000 + result


async def comp_hides_body_hoist(rows: list[int]) -> None:
    # A reference-type body name (a frame pointer) is also a manager
    # comprehension's variable; the alias is mutated after the with.
    with Items(len([ys for ys in rows])) as xs:  # tpyc: ok
        ys = xs
    ys.append(await value(42))
    # Elements, not the lists: printing a frame-pointer list whole rejects
    # (BUGS.md#frame-pointer-list-print-rejects).
    print("hoist_ref:", ys[0], ys[1], xs[1])


def main() -> None:
    print(asyncio.run(caller()))
    print("later_item:", asyncio.run(later_item_lambda()))
    asyncio.run(comp_hides_body_hoist([5, 6]))


main()
