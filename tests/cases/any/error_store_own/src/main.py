# Storing Own[NocopyT] in Any is rejected -- the underlying contents are
# non-copyable, and Any's std::any backing requires CopyConstructible.
# Verifies the move-only check unwraps Own when inspecting the contents type.

from typing import Any
from tpy import Own, nocopy


@nocopy
class Resource:
    def __init__(self, fd: int) -> None:
        self.fd = fd


def take_owned(r: Own[Resource]) -> None:
    a: Any = r  # tpyc: error(/cannot store move-only type/)
    print(a)


def main() -> None:
    take_owned(Resource(7))


main()
