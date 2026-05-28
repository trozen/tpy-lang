# Async method on a generic class. Two instantiations exercise template
# monomorphization.
import asyncio


class Box[T]:
    value: T

    def __init__(self, v: T) -> None:
        self.value = v

    async def take(self) -> T:  # tpyc: ok
        return self.value


def main() -> None:
    b = Box(7)
    print(asyncio.run(b.take()))
    s = Box("hi")
    print(asyncio.run(s.take()))


main()
