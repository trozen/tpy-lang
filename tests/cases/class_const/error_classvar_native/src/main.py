# Error: ClassVar on @native conflicts with C++-owned storage. Mutable
# extern statics belong on module-level via native_global.
from tpy.extern import native
from typing import ClassVar
from tpy import int32


@native
class BuildOpts:
    counter: ClassVar[int32] = 0  # tpyc: error(/ClassVar on @native classes is not supported/)
