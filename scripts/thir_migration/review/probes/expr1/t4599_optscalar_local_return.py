from tpy import int32, Own
class Container[T]:
    _val: T | None
    def __init__(self, val: T | None):
        self._val = val
    def get(self) -> T | None:
        return self._val
def make() -> Own[Container[int32]]:
    return Container[int32](None)
def f() -> int32 | None:
    c = make()
    x = c.get()
    return x
def main() -> None:
    pass
main()
