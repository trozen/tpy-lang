# A nested def in a method rebinding `self`: the method receiver has no
# rebindable storage behind it, so the rebind is refused.
from tpy import int32


class K:
    def __init__(self, n: int32) -> None:
        self.n = n

    def run(self) -> None:
        def inner() -> None:
            # The rebind of the receiver's name.
            self = K(2)  # tpyc: error(/Cannot rebind 'self' in a nested function/)
            print(self.n)
        inner()


K(1).run()
