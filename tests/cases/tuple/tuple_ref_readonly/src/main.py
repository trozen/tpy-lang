# @readonly method returning tuple with reference element
from tpy import int32, readonly

class Container:
    value: int32
    def __init__(self, value: int32) -> None:
        self.value = value
    def __repr__(self) -> str:
        return "Container(value=" + str(self.value) + ")"

class Wrapper:
    inner: Container
    def __init__(self, inner: Container) -> None:
        self.inner = inner

    @readonly
    def get_pair(self) -> tuple[readonly[Container], int32]:
        return (self.inner, self.inner.value)

def main() -> None:
    c = Container(int32(42))
    w = Wrapper(c)
    pair = w.get_pair()
    print(pair[0])
    print(pair[1])

main()
