# Constructor arguments use the current iteration's value, including lazy arms
# and loop else; borrowed record elements remain shared with the caller.
from tpy import int32, nocopy


@nocopy
class Cell:
    value: int32

    def __init__(self, value: int32):
        self.value = value


def read(cell: Cell) -> int32:
    return cell.value


def ranges(n: int32, stop: bool) -> int32:
    result = 0
    flag = True
    for i in range(n):
        # Alternating arms must use this iteration's constructor input.
        result = read(Cell(i)) if flag else read(Cell(9))  # tpyc: ok
        flag = not flag
        if stop:
            break
        continue
    else:
        # This executes on zero iterations and exhaustion, but not break.
        result = read(Cell(result)) + 1  # tpyc: ok
    return result


def update(values: list[Cell], n: int32):
    for cell in values:
        # Writing through the loop variable must update the caller's element.
        cell.value = read(Cell(n))  # tpyc: ok


def nested(n: int32, skip: bool) -> int32:
    result = 0
    for i in range(n):
        for j in range(0):
            # A zero-trip loop must skip its argument construction.
            result = read(Cell(j))  # tpyc: ok
        else:
            result = read(Cell(i))  # tpyc: ok
            # The inner loop has ended, so these transfers target the outer one.
            if skip:
                continue
            break
        result = 99
    else:
        # Exhaustion reaches this else; an inner-else break must skip it.
        result = read(Cell(7))  # tpyc: ok
    return result


class Runner:
    result: int32

    def __init__(self, n: int32):
        self.result = 0
        for i in range(n):
            # Constructor tails use the same per-iteration argument storage.
            self.result = read(Cell(i))  # tpyc: ok

    def method(self, values: list[int32]) -> int32:
        for value in values:
            # Method bodies also rebuild the argument from the current element.
            self.result = read(Cell(value))  # tpyc: ok
        return self.result


def main():
    print("range", ranges(0, False), ranges(1, False), ranges(4, False), ranges(4, True))
    values = [Cell(1), Cell(2)]
    update(values, 8)
    print("native", values[0].value, values[1].value)
    print("nested", nested(0, False), nested(3, False), nested(3, True))
    runner = Runner(3)
    print("constructor", runner.result)
    print("method", runner.method([3, 4]))


main()
