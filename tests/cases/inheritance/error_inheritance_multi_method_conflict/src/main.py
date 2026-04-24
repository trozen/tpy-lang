# Method conflict: both bases define foo() and the child does not override it.
# Non-virtual C++ MI leaves self.foo() ambiguous -- child must disambiguate.
from tpy import Int32


class Speaker:
    def greet(self) -> str:
        return "hello"


class Greeter:
    def greet(self) -> str:
        return "hi"


class Both(Speaker, Greeter):  # tpyc: error(/Method 'greet' defined by both 'Speaker' and 'Greeter'/)
    pass
