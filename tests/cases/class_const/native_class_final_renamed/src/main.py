# Phase 10: native_field("rename") on a @native class constant. The Python
# identifier is `FLAG`, but the C++ side has `g_flag`. Use sites resolve to
# `<native_qname>::g_flag` instead of `<native_qname>::FLAG`.
# tpy: include("native_types.hpp")
from tpy.extern import native, native_field
from typing import Final
from tpy import int32


@native
class BuildOpts:
    FLAG: Final[bool] = native_field("g_flag")
    MAX_RETRIES: Final[int32] = native_field("kMaxRetries")
    RELEASE_TAG: Final[str]  # no rename: defaults to RELEASE_TAG


def main() -> None:
    print(BuildOpts.FLAG)
    print(BuildOpts.MAX_RETRIES)
    print(BuildOpts.RELEASE_TAG)


main()
