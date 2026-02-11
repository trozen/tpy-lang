from tpy import Int32


class Box:
    value: Int32

    def __init__(self, value: Int32) -> None:
        self.value = value

    def get(self) -> Int32:
        return self.value


b = Box(Int32(123))
x = None
x = b.get()
print(x)
