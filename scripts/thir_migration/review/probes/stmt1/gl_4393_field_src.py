from tpy import int32
class Holder:
    xs: list[int32]
    def __init__(self) -> None:
        self.xs = [1, 2]
h: Holder = Holder()
ys: list[int32] = h.xs
print(len(ys))
