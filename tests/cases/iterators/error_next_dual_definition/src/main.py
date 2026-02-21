from typing import Optional

class MyIter:
    value: int

    def __init__(self, n: int) -> None:
        self.value = n

    def __next_opt__(self) -> Optional[int]:  # tpyc: ok
        if self.value <= 0:
            return None
        self.value = self.value - 1
        return self.value

    def __next__(self) -> int:  # tpyc: error(/Cannot define both/)
        if self.value <= 0:
            raise StopIteration
        self.value = self.value - 1
        return self.value
