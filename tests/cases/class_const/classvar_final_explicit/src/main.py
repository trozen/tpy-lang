# PEP 591 `ClassVar[Final[T]] = value` is the explicit form of `Final[T] = value`
# in a class body. Both produce a read-only `static constexpr` member.
from typing import ClassVar, Final
from tpy import Int32


class HttpClient:
    TIMEOUT: ClassVar[Final[Int32]] = 30
    MAX_RETRIES: ClassVar[Final[Int32]] = 5


def main() -> None:
    print(HttpClient.TIMEOUT)
    print(HttpClient.MAX_RETRIES)


main()
