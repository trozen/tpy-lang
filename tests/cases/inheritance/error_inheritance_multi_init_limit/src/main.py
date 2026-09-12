# When multiple bases define __init__, the child must define its own __init__
# and invoke each base explicitly via BaseN.__init__(self, ...).
from tpy import int32


class HasInitA:
    a: int32

    def __init__(self, a: int32) -> None:
        self.a = a


class HasInitB:
    b: int32

    def __init__(self, b: int32) -> None:
        self.b = b


class Combined(HasInitA, HasInitB):  # tpyc: error(/Multi-base class 'Combined' inherits __init__ from bases/)
    pass
