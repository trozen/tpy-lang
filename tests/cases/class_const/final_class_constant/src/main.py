# Pure-TPy class constants: Final[T] = value in a class body declares a
# class-scoped immutable static; reads via ClassName.X (PEP 591 implicit-ClassVar).
from typing import Final
from tpy import Int32


class HttpClient:
    TIMEOUT: Final[Int32] = 30
    MAX_RETRIES: Final[Int32] = 5
    DEFAULT_RATIO: Final[float] = 1.5
    ENABLED: Final[bool] = True


def main() -> None:
    print(HttpClient.TIMEOUT)
    print(HttpClient.MAX_RETRIES)
    print(HttpClient.DEFAULT_RATIO)
    print(HttpClient.ENABLED)


main()
