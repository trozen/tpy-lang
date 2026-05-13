# Aggregate record (no __init__) with a field whose type isn't
# default-constructible: zero-arg construction must be rejected by sema
# rather than producing an "implicitly deleted" C++ error or unsafe
# default-init at runtime.
from tpy import Int32, Own


class Holder:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x


class Outer:
    inner: Holder


def main() -> None:
    o = Outer()  # tpyc: error(/Outer\(\).*field 'inner'/)


main()
