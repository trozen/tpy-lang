# A coroutine cannot be stored into a field or container element directly;
# Box[Cancellable[T]] is the owned-storage spelling.
class Holder:
    slot: int

    def __init__(self) -> None:
        self.slot = 0


async def sub() -> int:
    return 42


def main() -> None:
    h = Holder()
    h.slot = sub()  # tpyc: error(/cannot be stored in a field/)
    print("ok")


main()
