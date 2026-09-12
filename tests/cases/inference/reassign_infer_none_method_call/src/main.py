from tpy import int32


class Box:
    value: int32

    def __init__(self, value: int32) -> None:
        self.value = value

    def get(self) -> int32:
        return self.value


b = Box(int32(123))
x = None
x = b.get()
print(x)
