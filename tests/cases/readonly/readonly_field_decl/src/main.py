# readonly[T] field declarations: init and read work, mutable fields still mutable
from tpy import readonly

class Config:
    name: readonly[str]
    value: int

    def __init__(self, name: str, value: int) -> None:
        self.name = name
        self.value = value

    def inc(self) -> None:
        self.value += 1

def main() -> None:
    c = Config("hello", 42)
    print(c.name, c.value)
    c.inc()
    print(c.value)
    c.value = 99
    print(c.value)

main()
