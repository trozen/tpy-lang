class MyIter:
    value: int

    def __init__(self, n: int) -> None:
        self.value = n

    def __next__(self):  # tpyc: error(/return type annotation/)
        if self.value <= 0:
            raise StopIteration
        self.value = self.value - 1
        return self.value
