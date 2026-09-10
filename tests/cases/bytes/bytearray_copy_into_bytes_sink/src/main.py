# The explicit `bytes(ba)` at every OWNING `bytes` sink -- the spelling the
# located error asks for (tests/cases/bytes/error_bytearray_at_bytes_sink).
# CPython's `bytes(ba)` copies too, so every section is parity-clean; mutating
# `ba` after the stores is what makes the copy observable, and both languages
# give the same answer. One prefixed line per sink so a divergence names the
# cell that moved. The element slot IS the `Own[bytes]` parameter face -- the
# free-function and constructor ones take only a bytes literal today
# (BUGS.md#own-bytes-param-takes-only-a-literal). The BORROWING direction is a
# free view over the caller's buffer and is not this case
# (tests/cases/generics/generic_slot_distinct_buffer_types). The literal,
# comprehension and WRAPPED (`bytes | None`) element slots are here too, since
# they are owning sinks the rule reaches at the leaf; the literal and Optional
# REJECTIONS have their own cases (bytes/error_bytearray_in_bytes_literal,
# bytes/error_bytearray_at_optional_bytes_slot).
# A tuple ELEMENT is an owning slot at every position the tuple sits at, so the
# tuple section below passes its tuple as a plain (BORROWING) argument on
# purpose; bytes/error_bytearray_in_bytes_tuple_arg pins the rejection there.
# The set-literal and dict-KEY faces cannot be witnessed at all: a bare
# bytearray is unhashable and is refused before the buffer rule is reached,
# which is CPython's answer too (TypeError: unhashable type: 'bytearray').
class Holder:
    data: bytes

    def __init__(self) -> None:
        self.data = b""

    def keep(self, src: bytearray) -> None:
        # the field sink -- in a method, not the constructor, because a native
        # call at a member-init field rejects
        # (BUGS.md#bytearray-ctor-field-and-list-conv)
        self.data = bytes(src)  # tpyc: ok


def own_return(ba: bytearray) -> bytes:
    # the return sink
    return bytes(ba)  # tpyc: ok


def tuple_arg(t: tuple[bytes, int]) -> int:
    b, n = t
    return len(b) + n


def main() -> None:
    ba = bytearray(b"ab")
    # the element sink -- `list.append` takes `Own[bytes]`
    seen: list[bytes] = []
    seen.append(bytes(ba))  # tpyc: ok
    # the local sink
    local: bytes = bytes(ba)  # tpyc: ok
    ret = own_return(ba)
    held = Holder()
    held.keep(ba)
    # Mutating the SOURCE after every store: each sink holds its own buffer.
    # the container-literal element sink
    lit: list[bytes] = [bytes(ba)]  # tpyc: ok
    # the dict-literal value sink. This one peer-unifies its values rather
    # than binding each against the annotation, so it does NOT enter the
    # wrapped branch the Optional sinks below exercise; both are kept.
    dlit: dict[str, bytes] = {"k": bytes(ba)}  # tpyc: ok
    # the comprehension element sink
    comp: list[bytes] = [bytes(ba) for _ in range(1)]  # tpyc: ok
    # the WRAPPED sinks: the buffer type sits under an Optional, so the rule
    # has to be asked at the leaf the unwrap reaches, not at the annotation
    opt: bytes | None = bytes(ba)  # tpyc: ok
    dopt: dict[str, bytes | None] = {"k": bytes(ba)}  # tpyc: ok
    # the TUPLE-element sink at a BORROWING position: the tuple is a plain
    # argument, but it holds its elements by value, so each one owns
    tup = tuple_arg((bytes(ba), 1))  # tpyc: ok
    ba.append(99)
    ba[0] = 122
    print("source", len(ba), ba[0])
    print("element", len(seen[0]), seen[0][0])
    print("local", len(local), local[0])
    print("return", len(ret), ret[0])
    print("field", len(held.data), held.data[0])
    print("literal", len(lit[0]), lit[0][0])
    print("dict", len(dlit["k"]), dlit["k"][0])
    print("comp", len(comp[0]), comp[0][0])
    print("optional", len(opt) if opt is not None else -1)
    # The dict-Optional value is only counted, not read back: reading an
    # Optional element out of a container does not lower yet
    # (BUGS.md#optional-container-element-read-unlowered). What that section
    # is here for is the STORE, which is the wrapped branch of the dict
    # literal; the copy itself is observed on `opt` above.
    print("dict_optional", len(dopt))
    print("tuple_arg", tup)


main()
