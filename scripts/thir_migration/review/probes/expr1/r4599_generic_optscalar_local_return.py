from tpy import Int32
class Container[T]:
    _val: T | None
    def __init__(self, val: T | None):
        self._val = val
    def get(self) -> T | None:
        return self._val
def f() -> Int32 | None:
    c = Container[Int32](5)
    x = c.get()
    return x
def main() -> None:
    pass
main()
