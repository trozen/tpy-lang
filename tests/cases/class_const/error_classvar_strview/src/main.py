# Error: ClassVar[StrView] is rejected because mutation can store a view
# into a temporary's storage. Use Final[StrView] for read-only constants.
from typing import ClassVar
from tpy import StrView


class Config:
    label: ClassVar[StrView] = "default"  # tpyc: error(/ClassVar\[StrView\] is not supported.*StrView is rejected because mutation can store a view/)
