# asyncio.run over an async def returning bytes: bytes is a TPy value type
# whose C++ backing (an owning vector) is not a C++ is_value_type, so run's
# `-> Own[T]` return (own_return_t<T>, by value) is what lets the owned
# payload move out of the task slot.
import asyncio


async def payload() -> bytes:
    await asyncio.sleep(0)
    return b"hi"


def main() -> None:
    print(asyncio.run(payload()))


main()
