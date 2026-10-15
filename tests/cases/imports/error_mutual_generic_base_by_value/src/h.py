class Hold[T]:
    value: T

    def __init__(self, value: T) -> None:
        self.value = value
