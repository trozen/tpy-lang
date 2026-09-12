# The dict sibling of set/elem_slot_view_key: `d[k]`, `.get`, `k in d`,
# `del d[k]` and `.pop` are LOOKUP slots, so a str/bytes key passes as a
# view and the runtime's transparent table finds the entry without building a
# key. The pin is the render -- no `std::string(...)` and no owned-bytes
# temporary at any of these calls -- plus the bytes half compiling at all
# (a `std::span` key had no `std::vector` to convert to).
# `d[k]` borrows the stored value, so `push_literal` mutates through it and
# the dict is printed afterwards; the other legs erase from the caller's dict.
from tpy import int32


def read_param(d: dict[str, list[int32]], k: str) -> int32:
    return d[k][0]  # tpyc: ok -- `k` is a std::string_view param


def get_param(d: dict[str, list[int32]], k: str) -> bool:
    return d.get(k) is not None  # tpyc: ok -- the same view key at .get


def has_param(d: dict[str, list[int32]], k: str) -> bool:
    return k in d  # tpyc: ok -- and at the membership read


def drop_param(d: dict[str, list[int32]], k: str) -> None:
    del d[k]  # tpyc: ok -- the erase compares the key, it does not build one


def push_literal(d: dict[str, list[int32]]) -> None:
    # A literal stays a bare `const char[N]`, which is a lookup form too; the
    # append lands in the dict's own list, not in a copy.
    d["a"].append(9)  # tpyc: ok


def pop_literal(d: dict[str, list[int32]]) -> int32:
    return d.pop("b")[0]  # tpyc: ok -- the free-template callee takes it bare


def empty_key(d: dict[str, int32], k: str) -> int32:
    # An empty key goes through the hash table, so it also pins that a view
    # and the stored owned key land in ONE hash domain.
    if k in d:
        return d[k]
    return -1


def read_bytes_param(d: dict[bytes, int32], k: bytes) -> int32:
    return d[k]  # tpyc: ok -- a std::span key probes the table directly


def get_bytes_param(d: dict[bytes, int32], k: bytes) -> bool:
    return d.get(k) is not None  # tpyc: ok


def drop_bytes_param(d: dict[bytes, int32], k: bytes) -> None:
    del d[k]  # tpyc: ok


def pop_bytes_param(d: dict[bytes, int32], k: bytes) -> int32:
    return d.pop(k)  # tpyc: ok


def main() -> None:
    d: dict[str, list[int32]] = {}
    d["a"] = [1]
    d["b"] = [2]
    d["c"] = [3]
    print(read_param(d, "a"), get_param(d, "zz"), has_param(d, "c"))
    push_literal(d)
    print(d["a"])
    drop_param(d, "c")
    print(pop_literal(d), len(d), d)

    e: dict[str, int32] = {}
    e[""] = 1
    e["a"] = 2
    print(empty_key(e, ""), empty_key(e, "zz"))

    b: dict[bytes, int32] = {}
    b[b"aa"] = 1
    b[b"bb"] = 2
    print(read_bytes_param(b, b"aa"), get_bytes_param(b, b"zz"))
    drop_bytes_param(b, b"aa")
    print(pop_bytes_param(b, b"bb"), len(b))


main()
