from tpy import Int32
class Holder:
    xs: list[Int32]
    def __init__(self) -> None:
        self.xs = [1, 2]
h: Holder = Holder()
ys: list[Int32] = h.xs
print(len(ys))
