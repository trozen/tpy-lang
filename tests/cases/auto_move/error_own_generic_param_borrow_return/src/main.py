# A borrow of an Own[...] param of a generic body dies at the return: an
# element of an owned list returned as a bare T lends into it at a class T,
# so it is refused as at the concrete twin (spell the return Own[T]).
from tpy import Own


def first_val[T](items: Own[list[T]]) -> T:
    return items[0]  # tpyc: error(/Cannot return local or temporary as reference/)


def main() -> None:
    pass


main()
