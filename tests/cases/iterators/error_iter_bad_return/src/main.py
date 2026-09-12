from tpy import int32

class NotAnIterator:
    value: int32

    def __init__(self) -> None:
        self.value = 0

class Container:
    def __init__(self) -> None:
        pass

    def __iter__(self) -> NotAnIterator:
        return NotAnIterator()

for x in Container():  # tpyc: error(/Cannot iterate/)
    print(x)
