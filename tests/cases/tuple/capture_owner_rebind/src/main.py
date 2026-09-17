# A tuple keeps the objects it captured when their owning local is rebound.
# Mutations distinguish saved references from both copies and replacement objects.
from tpy import int32
from typing import Iterator
import asyncio


class Cell:
    value: int32

    def __init__(self, value: int32):
        self.value = value


def borrowed(cell: Cell) -> tuple[Cell]:
    return (cell,)


def literal() -> None:
    current = Cell(1)
    saved = (current, current, current.value)  # tpyc: ok
    # This later use forces borrowed capture rather than last-use ownership.
    current.value = 4
    current = Cell(2)  # tpyc: ok
    print("literal before", saved[0].value, current.value, saved[2])
    saved[1].value = 9
    print("literal after", saved[0].value, current.value, saved[2])


def singleton() -> None:
    current = Cell(1)
    saved = (current,)  # tpyc: ok
    current.value = 4
    current = Cell(2)  # tpyc: ok
    saved[0].value = 9
    print("singleton", saved[0].value, current.value)


def copies() -> None:
    current = Cell(1)
    other = Cell(3)
    pair = (current,)
    saved = pair  # tpyc: ok
    # Reseating the original tuple must not redirect its copied reference.
    pair = (other,)  # tpyc: ok
    other.value = 5
    current.value = 4
    current = Cell(2)  # tpyc: ok
    saved[0].value = 9
    print("copies", saved[0].value, pair[0].value, current.value)


def reassigned() -> None:
    other = Cell(3)
    current = Cell(1)
    saved = (other,)
    other.value = 5
    # The replacement tuple creates a loan on current, not on the old element.
    saved = (current,)  # tpyc: ok
    current.value = 4
    current = Cell(2)  # tpyc: ok
    saved[0].value = 9
    print("reassigned", saved[0].value, current.value, other.value)


def self_assignment() -> None:
    current = Cell(1)
    saved = (current,)
    current.value = 4
    # A no-op binding retains the loan without creating a self-referential edge.
    saved = saved  # tpyc: ok
    current = Cell(2)  # tpyc: ok
    saved[0].value = 9
    print("self", saved[0].value, current.value)


def call_result() -> None:
    current = Cell(1)
    # Tuple results carry the callee's return-borrow contract into the holder.
    saved = borrowed(current)  # tpyc: ok
    current.value = 4
    current = Cell(2)  # tpyc: ok
    saved[0].value = 9
    print("call", saved[0].value, current.value)


def branch(flag: bool) -> None:
    current = Cell(1)
    saved = (current,)
    current.value = 4
    if flag:
        current = Cell(2)  # tpyc: ok
    else:
        current = Cell(3)  # tpyc: ok
    saved[0].value = 9
    print("branch", flag, saved[0].value, current.value)


def scalar_inverse() -> None:
    current = Cell(1)
    saved = (current.value,)
    # A scalar snapshot must not keep a loan on the source record.
    current = Cell(2)  # tpyc: ok
    print("scalar", saved[0], current.value)


def walrus() -> None:
    current = Cell(1)
    # This expression binding captures a reference just like a declaration.
    print("walrus scalar", (saved := (1, current))[0])  # tpyc: ok
    current.value = 4
    current = Cell(2)  # tpyc: ok
    saved[1].value = 9
    print("walrus", saved[1].value, current.value)


def moved_inverse() -> None:
    current = Cell(1)
    # Last use captures ownership; there is no borrowed member to register.
    saved = (current,)  # tpyc: ok
    current = Cell(2)  # tpyc: ok
    saved[0].value = 9
    print("moved", saved[0].value, current.value)


class Runner:
    result: int32

    def __init__(self):
        # Constructor-local capture has the same storage lifetime as a function.
        current = Cell(1)
        saved = (current,)
        current.value = 4
        current = Cell(2)  # tpyc: ok
        saved[0].value = 9
        self.result = current.value

    def run(self) -> None:
        # Method-local capture must retain its old object across the reseat.
        current = Cell(1)
        saved = (current,)
        current.value = 4
        current = Cell(2)  # tpyc: ok
        saved[0].value = 9
        print("method", saved[0].value, current.value, self.result)


def generic[T](seed: T) -> None:
    current = Cell(1)
    saved = (current,)
    current.value = 4
    # Instantiating the function must preserve the same capture loan.
    current = Cell(2)  # tpyc: ok
    saved[0].value = 9
    print("generic", seed, saved[0].value, current.value)


def suspended() -> Iterator[int32]:
    current = Cell(1)
    saved = (current,)
    current.value = 4
    yield saved[0].value
    # Resuming the frame must keep the saved object's storage separate.
    current = Cell(2)  # tpyc: ok
    saved[0].value = 9
    yield current.value


async def asynchronous() -> int32:
    current = Cell(1)
    saved = (current,)
    current.value = 4
    await asyncio.sleep(0)
    # Coroutine frame storage obeys the same owner-replacement verdict.
    current = Cell(2)  # tpyc: ok
    saved[0].value = 9
    return current.value


def closure() -> None:
    def inner() -> int32:
        current = Cell(1)
        saved = (current,)
        current.value = 4
        # A nested body must record loans in its own rebind analysis.
        current = Cell(2)  # tpyc: ok
        saved[0].value = 9
        return current.value
    print("closure", inner())


def main() -> None:
    literal()
    singleton()
    copies()
    reassigned()
    self_assignment()
    call_result()
    branch(True)
    branch(False)
    scalar_inverse()
    walrus()
    moved_inverse()
    runner = Runner()
    runner.run()
    generic(1)
    for value in suspended():
        print("generator", value)
    print("async", asyncio.run(asynchronous()))
    closure()


main()
