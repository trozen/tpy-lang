from tpy import int32

class Container[T]:
    value: T

    def __init__(self, value: T) -> None:
        self.value = value


# Missing type arguments for generic parent
class IntContainer(Container):  # tpyc: error(/requires type arguments/)
    extra: int32
