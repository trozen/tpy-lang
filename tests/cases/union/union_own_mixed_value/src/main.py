# Own[union] return with mixed value/non-value members.
# Value-type (Span) and Own-typed (list) results coexist in the return union.
from tpy import Int32, Span, Own

class Box:
    items: list[Int32]
    _dummy: Int32

    def __init__(self) -> None:
        self.items = [10, 20, 30, 40, 50]
        self._dummy = 0

    def get_span(self) -> Own[Span[Int32] | list[Int32]]:  # tpyc: ok
        """basic_slice produces Span (value type) -- allowed as Own return."""
        self._dummy += 1
        return self.items[1:4]  # tpyc: ok

    def get_list(self) -> Own[Span[Int32] | list[Int32]]:  # tpyc: ok
        """stepped slice produces Own[list] -- allowed as Own return."""
        self._dummy += 1
        return self.items[0:4:2]  # tpyc: ok

    def get_via_var(self) -> Own[Span[Int32] | list[Int32]]:  # tpyc: ok
        """Intermediate value-type variable returned as Own[union]."""
        self._dummy += 1
        result = self.items[0:3]  # tpyc: ok
        return result  # tpyc: ok

def main() -> None:
    b = Box()
    b.get_span()
    b.get_list()
    b.get_via_var()
    print("ok")

main()
