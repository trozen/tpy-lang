# dict[Any, V] accepts heterogeneous keys -- symmetric with how
# dict[K, Any] accepts heterogeneous values. Each key validates against
# Any individually rather than against its peers.

from typing import Any


def main() -> None:
    d: dict[Any, int] = {1: 1, "two": 2, 3.5: 3}
    print(len(d))


main()
