# Regression: print(x) on Optional[container] for list/dict/set/bytes/
# bytearray. The container types have no plain operator<< -- printing
# routes through ListPrinter / DictPrinter / SetPrinter / BytesPrinter /
# ByteArrayPrinter. For Optional sources, print_optional / print_optional_val
# now accept a Formatter template arg so the wrapper printer applies only
# when the optional has a value (vs printing "None").
from tpy import int32


class Bag:
    items: list[int] | None
    by_key: dict[str, int32] | None
    elems: set[int32] | None
    data: bytes | None
    buf: bytearray | None

    def __init__(
        self,
        items: list[int] | None,
        by_key: dict[str, int32] | None,
        elems: set[int32] | None,
        data: bytes | None,
        buf: bytearray | None,
    ) -> None:
        self.items = items
        self.by_key = by_key
        self.elems = elems
        self.data = data
        self.buf = buf

    def show_fields(self) -> None:
        print(self.items)
        print(self.by_key)
        print(self.elems)
        print(self.data)
        print(self.buf)


def show_list_param(lst: list[int] | None) -> None:
    print(lst)


def show_dict_param(d: dict[str, int32] | None) -> None:
    print(d)


def show_set_param(s: set[int32] | None) -> None:
    print(s)


def show_bytes_param(b: bytes | None) -> None:
    print(b)


def show_bytearray_param(b: bytearray | None) -> None:
    print(b)


def main() -> None:
    # Bind container literals to named locals first (Bag's Optional[container]
    # params take pointers; passing rvalue literals trips the same pre-existing
    # rvalue-address-of issue worked around in the param-call section below).
    init_items: list[int] = [1, 2]
    init_by_key: dict[str, int32] = {"a": 1}
    init_elems: set[int32] = {3, 4}
    init_buf = bytearray(b"yo")
    Bag(init_items, init_by_key, init_elems, b"hi", init_buf).show_fields()
    # None on all five inners
    Bag(None, None, None, None, None).show_fields()

    # Bind container rvalues to named locals first -- passing the literal
    # directly as an `Optional[container]` param hits a pre-existing
    # rvalue-address-of bug in container-literal -> pointer-param coercion.
    lst: list[int] = [10, 20]
    show_list_param(lst)
    show_list_param(None)
    d: dict[str, int32] = {"k": 7}
    show_dict_param(d)
    show_dict_param(None)
    s: set[int32] = {9}
    show_set_param(s)
    show_set_param(None)
    show_bytes_param(b"abc")
    show_bytes_param(None)
    buf = bytearray(b"ok")
    show_bytearray_param(buf)
    show_bytearray_param(None)


main()
