# Method with its own type param `[U]` on a generic class `Box[T]`. The
# coro struct's template header carries both (record first, then method:
# `template <typename T, typename U>`); a swapped order would deduce
# `Box<U>&` and miscompile.
import asyncio


class Box[T]:
    val: T

    def __init__(self, v: T) -> None:
        self.val = v

    async def with_label[U](self, label: U) -> U:
        return label


def main() -> None:
    b = Box(42)
    print(asyncio.run(b.with_label("hello")))
    print(asyncio.run(b.with_label(99)))


main()
