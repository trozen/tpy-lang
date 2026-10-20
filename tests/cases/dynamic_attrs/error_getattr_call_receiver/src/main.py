# A `__getattr__` fallback read off a call result is not lowered yet:
# BUGS.md#dyn-attr-fallback-rvalue-receiver-rejects. CPython prints 5.
from tpy import Own


class Bag:
    def __getattr__(self, name: str) -> int:
        return len(name)


def mk_bag() -> Own[Bag]:
    return Bag()


def main() -> None:
    # The receiver is a call, not a name.
    print(mk_bag().alpha)  # tpyc: error(/not yet supported.*getattr.recv_shape/)


main()
