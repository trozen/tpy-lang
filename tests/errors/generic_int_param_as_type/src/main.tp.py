class Container[T, N: int]:
    size: N  # tpyc: error(/cannot be used as a type/)

    def __init__(self) -> None:
        pass
