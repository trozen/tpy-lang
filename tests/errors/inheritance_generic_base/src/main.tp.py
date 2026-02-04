from tpy import Int32

class Container[T]:
    value: T

    def __init__(self, value: T) -> None:
        self.value = value


class IntContainer(Container[Int32]):  # tpyc: error(/Generic base class.*not yet supported/)
    extra: Int32
