# Aggregate (no __init__) inheriting from a parent whose __init__ requires
# args: zero-arg construction must be sema-rejected, citing the offending
# parent. Guards _validate_aggregate_zero_arg's parent-walk.
from tpy import Int32


class Base:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x


class Child(Base):
    tag: Int32 = 0


def main() -> None:
    c = Child()  # tpyc: error(/Child\(\).*parent 'Base'/)


main()
