from tpy import int32
class T:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x
t1 = T(10)
g: tuple[T | None, T | None] = (t1, None)
g2: tuple[T | None, T | None] = g
def use() -> None:
    print(0)
use()
