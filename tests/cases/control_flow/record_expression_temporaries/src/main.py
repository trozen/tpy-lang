# Constructor field reads stay inside the selected lazy operand and repeat in
# loop conditions; scalar results remain usable after each expression completes.
from tpy import int32, nocopy


@nocopy
class Cell:
    value: int32

    def __init__(self, value: int32):
        self.value = value


def free(flag: bool) -> int32:
    value = 0
    # The selected constructor observes the guard's write, with no eager copy.
    result = Cell(value).value if flag and (value := 3) == 3 else Cell(7).value  # tpyc: ok
    # Discarding this construction must neither shadow nor change value.
    Cell(value)  # tpyc: ok
    return Cell(result).value  # tpyc: ok


def condition(value: int32) -> int32:
    # Reevaluate the receiver after changing value, including the false test.
    while Cell(value).value == 1:  # tpyc: ok
        value = 2
    return value


class Runner:
    value: int32

    def __init__(self, value: int32):
        self.value = 0
        # Constructor-tail field assignment consumes the scalar before expiry.
        self.value = Cell(value).value  # tpyc: ok

    def read(self, flag: bool) -> int32:
        # The local result outlives either receiver temporary.
        result = Cell(self.value).value if flag else Cell(9).value  # tpyc: ok
        return result


def main():
    print("free", free(True), free(False))
    print("condition", condition(1), condition(0))
    runner = Runner(5)
    print("constructor", runner.value)
    print("method", runner.read(True), runner.read(False))


main()
