from tpy import Int32

# next() takes a parameter — should not be iterable
class BadArity:
    value: Int32

    def __init__(self) -> None:
        self.value = 0

    def next(self, step: Int32) -> Int32 | None:
        self.value += step
        return self.value

for x in BadArity():  # tpyc: error(/Cannot iterate/)
    print(x)
