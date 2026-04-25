# When multiple bases define __init__, the child must define its own __init__
# and invoke each base explicitly via BaseN.__init__(self, ...).
from tpy import Int32


class HasInitA:
    a: Int32

    def __init__(self, a: Int32) -> None:
        self.a = a


class HasInitB:
    b: Int32

    def __init__(self, b: Int32) -> None:
        self.b = b


class Combined(HasInitA, HasInitB):  # tpyc: error(/Multi-base class 'Combined' inherits __init__ from bases/)
    pass
