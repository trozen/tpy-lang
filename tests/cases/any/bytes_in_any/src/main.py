# bytes stored in Any: print/str/repr route through __str__(Bytes)
# producing Python's b'...' representation.

from typing import Any


def main() -> None:
    a: Any = b"hi"
    print(a)


main()
