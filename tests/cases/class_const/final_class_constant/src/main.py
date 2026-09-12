# Pure-TPy class constants: Final[T] = value in a class body declares a
# class-scoped immutable static; reads via ClassName.X (PEP 591 implicit-ClassVar).
from typing import Final
from tpy import int32


class HttpClient:
    TIMEOUT: Final[int32] = 30
    MAX_RETRIES: Final[int32] = 5
    DEFAULT_RATIO: Final[float] = 1.5
    ENABLED: Final[bool] = True


def main() -> None:
    print(HttpClient.TIMEOUT)
    print(HttpClient.MAX_RETRIES)
    print(HttpClient.DEFAULT_RATIO)
    print(HttpClient.ENABLED)


main()
