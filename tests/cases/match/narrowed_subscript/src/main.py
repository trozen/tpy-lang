# Narrowed union provenance must preserve member-specific checked indexing.
# Scalar reads keep value semantics in ordinary and resumable bodies.
import asyncio
from typing import Iterator

from tpy import Int32, Own, ReturnException, error_return


# Free functions: original subjects, isinstance twins, and runtime BigInt indices.
def string_reads(x: str | Int32, index: int) -> None:
    match x:
        case str():
            print("str-match", x[0], x[-1], x[index])  # tpyc: ok
            print("str-slice", x[1:])  # tpyc: ok
            try:
                print(x[index + 10])  # tpyc: ok
            except IndexError:
                print("str-match-bounds")
        case _:
            print("str-other")
    if isinstance(x, str):
        print("str-isinstance", x[0], x[-1], x[index])  # tpyc: ok
        try:
            print(x[-10])  # tpyc: ok
        except IndexError:
            print("str-isinstance-bounds")


class Indexed:
    def __getitem__(self, index: Int32) -> Int32:
        return index + 20


def bytes_reads(x: bytes | Indexed, index: int) -> None:
    match x:
        case bytes():
            print("bytes-match", x[0], x[-1], x[index])  # tpyc: ok
            print("bytes-slice", x[1:])  # tpyc: ok
            try:
                print(x[index + 10])  # tpyc: ok
            except IndexError:
                print("bytes-match-bounds")
        case _:
            print("bytes-other")
    if isinstance(x, bytes):
        print("bytes-isinstance", x[0], x[-1], x[index])  # tpyc: ok


# BUGS.md#bytes-value-union-boundary-rejects blocks callers; this body still builds.
def bytes_value_union(x: bytes | Int32) -> None:
    match x:
        case bytes():
            print("bytes-value-union", x[0])  # tpyc: ok
        case _:
            pass


def bytearray_reads(x: bytearray | Int32, index: int) -> None:
    match x:
        case bytearray():
            print("bytearray-match", x[0], x[-1], x[index])  # tpyc: ok
            try:
                print(x[index + 10])  # tpyc: ok
            except IndexError:
                print("bytearray-match-bounds")
        case _:
            print("bytearray-other")
    if isinstance(x, bytearray):
        print("bytearray-isinstance", x[0], x[-1], x[index])  # tpyc: ok
        try:
            print(x[-10])  # tpyc: ok
        except IndexError:
            print("bytearray-isinstance-bounds")


# As-captures and guarded arms use different narrowing producers.
def capture_reads(x: str | Int32, allowed: bool) -> None:
    match x:
        case str() as captured if allowed:
            print("capture", captured[0], captured[-1])  # tpyc: ok
        case _:
            print("capture-other")


def generic_read[T](marker: T) -> None:
    x = union_text(True)
    match x:
        case str():
            print("generic", x[0], marker)  # tpyc: ok
        case _:
            pass


# A generic enclosing body and scalar/tuple sinks keep the same indexed value.
def monomorphic_read(marker: Int32) -> None:
    x = union_text(True)
    match x:
        case str():
            print("monomorphic", x[0], marker)  # tpyc: ok
        case _:
            pass


def tuple_reads(x: bytearray | Int32) -> None:
    match x:
        case bytearray():
            one = (x[0],)  # tpyc: ok
            two = (x[0], x[-1])  # tpyc: ok
            print("tuple", x[0], one[0], two[0], two[1])
        case _:
            pass


class Reader:
    value: str

    # Constructor: the indexed scalar is consumed by a field store.
    def __init__(self, x: str | Int32) -> None:
        self.value = ""
        match x:
            case str():
                self.value = str(x[0])  # tpyc: ok
            case _:
                pass

    # Method: readonly receiver inference must not affect scalar indexing.
    def read(self, x: str | Int32) -> str:
        match x:
            case str():
                return str(x[-1])  # tpyc: ok
            case _:
                return ""


# Generator: reads on both sides of a suspension use the extracted member.
def generate(x: bytearray | Int32) -> Iterator[Int32]:
    match x:
        case bytearray():
            yield x[0]  # tpyc: ok
            yield x[-1]  # tpyc: ok
        case _:
            yield -1


# Async: a resumed arm restores the same member-specific indexing route.
async def async_read(x: bytearray | Int32) -> Int32:
    match x:
        case bytearray():
            first = x[0]  # tpyc: ok
            await asyncio.sleep(0)
            return first + x[-1]  # tpyc: ok
        case _:
            return -1


# Comprehension: each element consumes the scalar, not the original union.
def comprehension(x: bytearray | Int32) -> None:
    match x:
        case bytearray():
            values = [x[i] for i in range(3)]  # tpyc: ok
            print("comprehension", values)
        case _:
            pass


def union_text(choose_text: bool) -> str | Int32:
    if choose_text:
        return "abc"
    return 1


# Closure: a local union keeps narrowing inside the nested body.
# Union parameters reject here: BUGS.md#nested-def-optional-param.
def closure(choose_text: bool) -> None:
    def read() -> None:
        y = union_text(choose_text)
        match y:
            case str():
                print("closure", y[-1])  # tpyc: ok
            case _:
                pass

    read()


class Guard:
    def __enter__(self) -> "Guard":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


# Context-manager, try and finally bodies preserve the extraction alias.
def cleanup_reads(x: str | Int32) -> None:
    match x:
        case str():
            with Guard():
                print("context", x[0])  # tpyc: ok
            try:
                print("try", x[1])  # tpyc: ok
            finally:
                print("finally", x[-1])  # tpyc: ok
        case _:
            pass


class Err(Exception, ReturnException):
    pass


# Error-return body: the normal result remains a scalar value.
@error_return(Err)
def error_read(x: bytearray | Int32) -> Int32:
    match x:
        case bytearray():
            return x[1]  # tpyc: ok
        case _:
            raise Err


class Counter:
    calls: Int32

    def __init__(self) -> None:
        self.calls = 0

    def index(self) -> Int32:
        self.calls += 1
        return 1


# Conditional operands: skipped reads cannot throw or evaluate their index.
def conditional_reads(x: str | Int32, counter: Counter) -> None:
    match x:
        case str():
            print("and", False and x[99] == "x")  # tpyc: ok
            print("or", True or x[99] == "x")  # tpyc: ok
            print("ternary", x[counter.index()] if counter.calls == 0 else x[99])  # tpyc: ok
            print("index-calls", counter.calls)
        case _:
            pass


# Sibling controls: container and record dispatch already admit narrowed reads.
def list_read(x: list[Int32] | Int32) -> None:
    match x:
        case list():
            print("list", x[0], x[-1])  # tpyc: ok
            try:
                print(x[99])  # tpyc: ok
            except IndexError:
                print("list-bounds")
        case _:
            pass


def dict_read(x: dict[Int32, Int32] | Int32) -> None:
    match x:
        case dict():
            print("dict", x[0])  # tpyc: ok
            try:
                print(x[99])  # tpyc: ok
            except KeyError:
                print("dict-missing")
        case _:
            pass


def record_read(x: Indexed | Int32) -> None:
    match x:
        case Indexed():
            print("record", x[0])  # tpyc: ok
        case _:
            pass


def make_buffer() -> Own[bytearray | Int32]:
    buffer = bytearray(b"abc")
    return buffer


def make_list() -> Own[list[Int32] | Int32]:
    items: list[Int32] = [10, 20]
    return items


def main() -> None:
    text: str | Int32 = "abc"
    buffer = make_buffer()
    index = int("1")
    string_reads(text, index)
    bytes_reads(b"abc", index)
    bytearray_reads(buffer, index)
    capture_reads(text, True)
    capture_reads(text, False)
    marker = 7
    generic_read(marker)  # tpyc: ok
    monomorphic_read(marker)
    tuple_reads(buffer)
    reader = Reader(text)
    print("constructor", reader.value)
    print("method", reader.read(text))
    for value in generate(buffer):
        print("generator", value)
    print("async", asyncio.run(async_read(buffer)))
    comprehension(buffer)
    closure(True)
    cleanup_reads(text)
    try:
        print("error-return", error_read(buffer))
    except Err:
        print("error-return-other")
    counter = Counter()
    conditional_reads(text, counter)
    items = make_list()
    indexed = Indexed()
    list_read(items)
    dict_read({0: 30})
    record_read(indexed)


main()

# Module-level storage has a distinct lowering route from function locals.
global_text = union_text(True)
match global_text:
    case str():
        print("module", global_text[0], global_text[-1])  # tpyc: ok
    case _:
        pass
