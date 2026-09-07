# A static method returning `Own[T] | None` consumed as a plain call
# ARGUMENT: the storage escape that admits the return is storage-gated, and
# an argument is not a storage sink.
from tpy import Int32, Own


class Container[T]:
    value: T

    def __init__(self, value: Own[T]) -> None:
        self.value = value

    @staticmethod
    def wrap_optional(v: Own[T] | None) -> Own[Container[T]] | None:
        if v is not None:
            return Container(v)
        return None


def check(c: Container[Int32] | None) -> bool:
    return c is not None


def main() -> None:
    # The optional-own rvalue lands at an argument slot.
    print(check(Container.wrap_optional(99)))  # tpyc: error(/stmt\.expr_stmt:call\.arg_shape\.optional/)


main()
