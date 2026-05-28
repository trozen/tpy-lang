# @readonly async method on a generic class. Exercises the
# `const Box<T>&` self-capture branch in the resumable frame.
import asyncio
from tpy import readonly


class Box[T]:
    val: T

    def __init__(self, v: T) -> None:
        self.val = v

    @readonly
    async def peek(self) -> T:
        return self.val


def main() -> None:
    b = Box(42)
    print(asyncio.run(b.peek()))
    s = Box("hi")
    print(asyncio.run(s.peek()))


main()
