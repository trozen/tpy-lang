# Fresh records stay inline in local tuples; indexed writes reach those records.
# @nocopy makes an unintended copy fail in functions, methods and constructor tails.
from tpy import int32, nocopy


@nocopy
class Cell:
    value: int32

    def __init__(self, value: int32):
        self.value = value


def free() -> int32:
    # The singleton owns its fresh record without requiring a copy.
    pair = (Cell(1),)  # tpyc: ok
    pair[0].value = 9  # tpyc: ok
    return pair[0].value


def branch(flag: bool, value: int32) -> int32:
    if flag:
        value = 5
        # Constructor operands are read here, after the guard and the write.
        pair = (Cell(value), 7, Cell(3), True)  # tpyc: ok
        value = 8
        pair[2].value = 11  # tpyc: ok
        return pair[0].value if pair[3] else pair[1]
    return 0


def repeated(again: bool) -> int32:
    result = 0
    while True:
        # Each loop-body activation constructs a new tuple with two records.
        pair = (Cell(2), Cell(3))  # tpyc: ok
        pair[1].value = 7  # tpyc: ok
        result = pair[1].value
        if again:
            again = False
            continue
        break
    return result


class Runner:
    result: int32

    def __init__(self, flag: bool):
        self.result = 0
        if flag:
            # A constructor tail uses the same local tuple storage as a function.
            pair = (Cell(1),)  # tpyc: ok
            pair[0].value = 12  # tpyc: ok
            self.result = pair[0].value

    def method(self, flag: bool) -> int32:
        if flag:
            # A method's indexed write also reaches the inline record.
            pair = (Cell(2), 8)  # tpyc: ok
            pair[0].value = 13  # tpyc: ok
            return pair[0].value
        return 0


def main() -> None:
    print("free", free())
    print("branch", branch(True, 2), branch(False, 2))
    print("loop", repeated(True), repeated(False))
    runner = Runner(True)
    skipped = Runner(False)
    print("constructor", runner.result, skipped.result)
    print("method", runner.method(True), runner.method(False))


main()
