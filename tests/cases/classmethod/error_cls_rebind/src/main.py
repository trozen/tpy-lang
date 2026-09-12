# `cls` must stay a stable alias for the defining class, so rebinding it to a
# value inside the body is rejected.
from tpy import int32


class P:
    def __init__(self, x: int32):
        self.x = x

    @classmethod
    def bad(cls) -> int32:
        cls = 5  # tpyc: error(/Cannot rebind 'cls'/)
        return cls


def main() -> None:
    print(P.bad())


main()
