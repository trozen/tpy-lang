# Explicit copies made in loops own independent records; their mutations must
# leave the source unchanged, while a retained alias keeps the replaced original.
from tpy import int32, copy, readonly
import tpy


class Cell:
    value: int32

    def __init__(self, value: int32):
        self.value = value


def copied(source: readonly[Cell], count: int32) -> int32:
    result = source.value
    for i in range(count):
        # Mutating each explicit copy must leave the readonly source unchanged.
        duplicate = copy(source)  # tpyc: ok
        duplicate.value = i
        result = source.value
    return result


def replaced(count: int32) -> int32:
    current = Cell(1)
    for i in range(count):
        # Read the old payload before replacing it; every iteration must see the previous write.
        current = Cell(2 if current.value == 1 else 1)  # tpyc: ok
    return current.value


def retained() -> int32:
    current = Cell(3)
    original = current
    # A copied alias would retain 3 instead of observing this shared mutation.
    current.value = 9
    for i in range(3):
        # The live alias keeps the old record, so replacement must use different storage.
        current = Cell(i)  # tpyc: ok
    return original.value if current.value == 2 else 0


class Runner:
    value: int32

    def __init__(self, count: int32):
        self.value = 0
        source = Cell(5)
        for i in range(count):
            # Constructor-tail copies are independent just like free-function copies.
            duplicate = copy(source)  # tpyc: ok
            duplicate.value = i
            self.value = source.value

    def method(self, source: Cell, count: int32) -> int32:
        result = 0
        for i in range(count):
            # The module-qualified spelling preserves the same copy before source mutation.
            duplicate = tpy.copy(source)  # tpyc: ok
            source.value = i
            result = duplicate.value
        self.value = result
        return result


def main():
    source = Cell(7)
    print("copy", copied(source, 0), copied(source, 3), source.value)
    print("replace", replaced(0), replaced(1), replaced(2), replaced(3))
    print("retained", retained())
    runner = Runner(3)
    print("constructor", runner.value)
    print("method", runner.method(source, 3), source.value)


main()
