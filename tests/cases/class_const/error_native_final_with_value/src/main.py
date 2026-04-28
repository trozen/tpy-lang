# Error: Final[T] with an initializer on @native class conflicts with C++-owned storage.
from tpy.extern import native
from typing import Final


@native
class BuildOpts:
    FLAG: Final[bool] = True  # tpyc: error(/Final initializer conflicts with C..-owned storage/)
