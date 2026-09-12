# @native class with `Final[T]` (no value): bind to C++ static members.
# TPy emits no struct body; use sites resolve to <native_name>::<member>.
# tpy: include("native_types.hpp")
from tpy.extern import native
from typing import Final
from tpy import int32


@native
class BuildOpts:
    FLAG: Final[bool]
    MAX_RETRIES: Final[int32]
    RELEASE_TAG: Final[str]


def main() -> None:
    print(BuildOpts.FLAG)
    print(BuildOpts.MAX_RETRIES)
    print(BuildOpts.RELEASE_TAG)


main()
