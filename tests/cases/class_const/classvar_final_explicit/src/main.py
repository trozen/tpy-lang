# PEP 591 `ClassVar[Final[T]] = value` is the explicit form of `Final[T] = value`
# in a class body. Both produce a read-only `static constexpr` member.
from typing import ClassVar, Final
from tpy import int32


class HttpClient:
    TIMEOUT: ClassVar[Final[int32]] = 30
    MAX_RETRIES: ClassVar[Final[int32]] = 5


def main() -> None:
    print(HttpClient.TIMEOUT)
    print(HttpClient.MAX_RETRIES)


main()
