from tpy import Int32

class Foo:
    value: Int32

    def __init__(self) -> None:
        self.value = 0

    def some_method(self) -> Int32:
        raise StopIteration  # tpyc: error(/raise StopIteration.*requires.*error_return/)
