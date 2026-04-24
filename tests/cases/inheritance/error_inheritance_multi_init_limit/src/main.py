# D22 v1 restriction: at most one base may define/inherit __init__. Lifting this
# is tracked in TODO.md (explicit BaseN.__init__(self, ...) call support).
from tpy import Int32


class HasInitA:
    a: Int32

    def __init__(self, a: Int32) -> None:
        self.a = a


class HasInitB:
    b: Int32

    def __init__(self, b: Int32) -> None:
        self.b = b


class Combined(HasInitA, HasInitB):  # tpyc: error(/Multi-base class 'Combined' has __init__ on more than one base/)
    pass
