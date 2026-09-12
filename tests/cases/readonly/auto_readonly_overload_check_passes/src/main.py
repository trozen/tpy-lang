# Regression guard: auto_readonly / auto_own clones generate two
# FunctionInfos sharing positional param types but differing in
# `is_readonly` (or `is_consuming`). The same-param-diff-return check
# must skip these since C++ realises them with `&` / `const &` / `&&`
# qualifiers and produces no ambiguity.
from tpy import int32, auto_readonly


class Box:
    value: int32

    def __init__(self) -> None:
        self.value = int32(0)

    # @auto_readonly generates a const-qualified clone alongside the
    # mutable original. Both clones have identical positional params
    # (just `self`) and identical return types -- the only difference
    # is the synthesised `is_readonly` flag.
    @auto_readonly
    def get(self) -> int32:
        return self.value


def main() -> None:
    b = Box()
    b.value = int32(42)
    print(b.get())  # 42


main()
