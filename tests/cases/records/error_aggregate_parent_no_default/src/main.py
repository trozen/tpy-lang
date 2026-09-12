# Aggregate (no __init__) inheriting from a parent whose __init__ requires
# args: zero-arg construction must be sema-rejected, citing the offending
# parent. Guards _validate_aggregate_zero_arg's parent-walk.
from tpy import int32


class Base:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class Child(Base):
    tag: int32 = 0


def main() -> None:
    c = Child()  # tpyc: error(/Child\(\).*parent 'Base'/)


main()
