# A view-form source (BytesView slice) materialized into an owned sink must
# evaluate the source ONCE -- regression for a codegen that double-evaluated it.
from typing import Any

calls = 0


def make() -> bytes:
    global calls
    calls += 1
    return b"abcde"


class Holder:
    b: bytes
    s: str

    def __init__(self):
        self.b = b""
        self.s = ""

    def set_bytes(self, x: bytes) -> None:
        self.b = x[1:3]

    def set_str(self, x: str) -> None:
        self.s = x[1:3]


def single_eval_into_list() -> None:
    out: list[bytes] = []
    out.append(make()[1:3])
    print(calls, len(out[0]))


def single_eval_into_field() -> None:
    global calls
    calls = 0
    h = Holder()
    h.b = make()[0:2]
    print(calls, len(h.b))


def single_eval_into_bytearray() -> None:
    global calls
    calls = 0
    ba: bytearray = make()[1:3]
    print(calls, len(ba))


def single_eval_into_any() -> None:
    global calls
    calls = 0
    items: list[Any] = []
    items.append(make()[1:3])
    print(calls, len(items))


def main() -> None:
    single_eval_into_list()
    single_eval_into_field()
    single_eval_into_bytearray()
    single_eval_into_any()
    h = Holder()
    h.set_bytes(b"hello")
    h.set_str("world")
    print(len(h.b), h.s)


main()
