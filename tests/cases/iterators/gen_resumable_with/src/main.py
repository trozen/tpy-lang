# with-statement generators on the resumable frame: __enter__/__exit__ around a
# yield, and item targets / comprehensions in a manager expression binding right.
from typing import Callable, Iterator

class Tracer:
    def __init__(self, label: str) -> None:
        self.label = label

    def __enter__(self) -> None:
        print("enter", self.label)

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        print("exit", self.label)

def gen_with_yield(xs: list[int]) -> Iterator[int]:
    with Tracer("g"):
        for x in xs:
            yield x

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

def gen_with() -> Iterator[int]:
    # A later item's manager is a lambda reading an earlier item's target.
    with Count(7) as n, Cb(lambda: n) as result:  # tpyc: ok
        yield n
        yield result

def gen_comp_target(xs: list[int]) -> Iterator[int]:
    # A manager comprehension's variable has the item target's name.
    with Count(sum([n for n in xs])) as n:  # tpyc: ok
        yield n

def gen_with_then_yield(xs: list[int]) -> Iterator[int]:
    # A with holding no yield, then a yield: the with lowers inside the frame.
    with Count(sum([n for n in xs])) as n:  # tpyc: ok
        pass
    yield n

class Items:
    items: list[int]

    def __init__(self, v: int) -> None:
        self.items = [v]

    def __enter__(self) -> list[int]:
        return self.items

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass

def gen_hoist_ref(rows: list[int]) -> Iterator[int]:
    # A reference-type body name (a frame pointer) is also a manager
    # comprehension's variable; the alias is mutated after the with.
    with Items(len([ys for ys in rows])) as xs:  # tpyc: ok
        ys = xs
    yield len(ys)
    ys.append(42)
    yield ys[1] + xs[1]

def main():
    for v in gen_with_yield([10, 20, 30]):
        print(v)
    print("gen_with:", list(gen_with()))
    print("gen_comp_target:", list(gen_comp_target([1, 2])))
    print("gen_with_then_yield:", list(gen_with_then_yield([1, 2])))
    print("gen_hoist_ref:", list(gen_hoist_ref([5, 6])))

main()
