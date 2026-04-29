# Storing a @nocopy record in Any is rejected -- v1 supports copyable
# contents only.

from typing import Any
from tpy import nocopy


@nocopy
class Resource:
    def __init__(self, fd: int) -> None:
        self.fd = fd


def main() -> None:
    r = Resource(7)
    a: Any = r  # tpyc: error(/cannot store move-only type/)
    print(a)


main()
