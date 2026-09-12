# Error: method default value 0 is incompatible with T=str
from tpy import int32, copy

class Container[T]:
    val: T

    def __init__(self, val: T) -> None:
        self.val = copy(val)

    def get_or(self, fallback: T = 0) -> T:
        return fallback

def main() -> None:
    c = Container[int32](10)
    print(c.get_or())

    s = Container[str]("hello")
    print(s.get_or())  # tpyc: error(/incompatible/)

main()
