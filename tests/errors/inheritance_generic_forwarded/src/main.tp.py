from tpy import Int32

class Container[T]:
    value: T

    def __init__(self, value: T) -> None:
        self.value = value


class Wrapper[T](Container[T]):  # tpyc: error(/Generic base class with forwarded type parameters not yet supported/)
    extra: Int32
