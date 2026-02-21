from tpy import Int32

# __next_opt__() returns non-optional — should not be iterable
class BadReturn:
    value: Int32

    def __init__(self) -> None:
        self.value = 0

    def __next_opt__(self) -> Int32:
        self.value += 1
        return self.value

for x in BadReturn():  # tpyc: error(/Cannot iterate/)
    print(x)
