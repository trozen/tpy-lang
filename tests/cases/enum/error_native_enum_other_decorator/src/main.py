# Only @native is allowed on an enum; other decorators must be rejected.
from enum import Enum
from tpy import nocopy


@nocopy  # tpyc: error(/Decorators are not supported on enum.*only @native/)
class E(Enum):
    A = 0
    B = 1


def main() -> None:
    pass


main()
