from tpy import ValueType


class W[T: ValueType](ValueType):
    p: T

    def __init__(self, p: T) -> None:
        self.p = p
