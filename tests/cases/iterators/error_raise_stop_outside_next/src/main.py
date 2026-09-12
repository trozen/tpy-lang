from tpy import int32

class Foo:
    value: int32

    def __init__(self) -> None:
        self.value = 0

    def some_method(self) -> int32:
        raise StopIteration  # tpyc: error(/raise StopIteration.*requires.*error_return/)
