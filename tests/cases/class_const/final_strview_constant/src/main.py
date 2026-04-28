# Final[str] in a class body unwraps to StrView (PEP 591 implicit-ClassVar +
# the same str -> StrView treatment as module-level Final).
from typing import Final


class HttpClient:
    USER_AGENT: Final[str] = "tpy/0.1"
    SCHEME: Final[str] = "https"


def main() -> None:
    print(HttpClient.USER_AGENT)
    print(HttpClient.SCHEME)


main()
