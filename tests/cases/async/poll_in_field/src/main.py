# Storing Poll[T] in a reference-type field -- exercises the storage-form
# path (Poll[T] is @nocopy, is_value_type=False).
from typing import Self
from tpy import Own, Int32, nocopy
from tpy.coro import Poll


@nocopy
class Holder:
    _slot: Poll[Int32]

    def __init__(self, p: Own[Poll[Int32]]) -> None:
        self._slot = p

    def take(self: Own[Self]) -> Own[Poll[Int32]]:
        return self._slot


def main() -> None:
    h = Holder(Poll[Int32].ready(42))
    p = h.take()
    print("ready:", p.is_ready())
    print("value:", p.value())


main()
