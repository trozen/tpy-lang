# Test that unknown keyword arguments on @native are rejected
from tpy import Int32
from tpy.extern import native

@native("std::vector")
class Vec[T]:
    @native("push_back", typo=True)  # tpyc: error(/unexpected keyword argument 'typo'/)
    def add(self, value: Int32) -> None: ...
