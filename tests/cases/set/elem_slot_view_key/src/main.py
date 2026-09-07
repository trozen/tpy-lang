# The set sibling of list/elem_slot_view_key: `discard`/`remove` and the
# membership read are LOOKUP slots, so a str lookup key passes as a view --
# or, for a literal, as the bare `const char[N]` -- and `ordered_set::erase` /
# `contains` take the `lookup_key_for` arm, which probes the transparent
# table with the key instead of building an element.
# What the snapshot pins is the RENDER: no `std::string(...)` at any of these
# calls. A snapshot cannot pin the non-allocation itself (that is which
# overload wins, not what the call looks like); `lookup_key.hpp`'s
# static_asserts hold that half.
# The discard/remove legs mutate the caller's set so the aliasing is
# observable; the membership leg reads.


def discard_param(s: set[str], k: str) -> None:
    s.discard(k)  # tpyc: ok -- `k` is a std::string_view param


def discard_literal(s: set[str]) -> None:
    s.discard("b")  # tpyc: ok -- a member call's literal stays bare


def remove_param(s: set[str], k: str) -> None:
    s.remove(k)  # tpyc: ok


def remove_literal(s: set[str]) -> None:
    s.remove("c")  # tpyc: ok -- the free-template callee takes it bare too


def has(s: set[str], k: str) -> bool:
    return k in s  # tpyc: ok -- the same view key at the membership read


def empty_key(s: set[str], k: str) -> None:
    # An empty key goes through the hash table, so it also pins that a view
    # and the stored owned string land in ONE hash domain.
    print(k in s)
    s.discard(k)
    print(k in s, len(s))


def main() -> None:
    s: set[str] = set()
    s.add("a")
    s.add("b")
    s.add("c")
    discard_param(s, "a")
    print(len(s))
    discard_literal(s)
    print(len(s))
    remove_literal(s)
    print(len(s))

    t: set[str] = set()
    t.add("x")
    t.add("y")
    remove_param(t, "x")
    print(len(t))
    print(has(t, "y"), has(t, "x"))

    e: set[str] = set()
    e.add("")
    e.add("a")
    empty_key(e, "")


main()
