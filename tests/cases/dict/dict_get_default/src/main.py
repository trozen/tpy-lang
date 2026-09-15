# Test d.get(key, default) -- returns value if present, default otherwise.
# The default slot is declared `default: V`, the BORROW form, so a source whose
# own form differs from V's storage still binds there and the native builds the
# owned V it hands back: the `str` legs pass a `String` lvalue and a `char`,
# neither of which IS `std::string`. (The runtime-side deduction -- that V comes
# from the MAP and never from the default -- is pinned at its source by
# `runtime/cpp/tests/test_dict_default_args.cpp`; a missing spelling there is a
# toolchain error with no TPy location.)
# The bytes legs spell the copy explicitly (`bytes(ba)`): the bare bytearray
# also binds today, but it hands back a fresh bytes where CPython hands back
# the caller's bytearray, silently
# (BUGS.md#bytearray-at-bytes-param-owned-silently).
from tpy import String, char, int32

def main() -> None:
    d: dict[str, int32] = {"a": 1, "b": 2}
    print(d.get("a", 99))     # 1
    print(d.get("z", 99))     # 99
    print(d.get("b", 0))      # 2

    s: dict[str, str] = {"a": "alpha"}
    lv = String("lvalue")
    # a default whose form is not V's storage: a String lvalue, then a char
    print(s.get("a", lv))          # tpyc: ok
    print(s.get("z", lv))          # tpyc: ok
    print(s.pop("z", char("q")))   # tpyc: ok

    b: dict[str, bytes] = {"a": b"hi"}
    ba = bytearray(b"zz")
    # the subject: a bytearray copied into the bytes default slot -- the legs
    # print the value, so a divergence from CPython cannot hide behind a bool
    print(b.get("a", bytes(ba)))   # tpyc: ok
    print(b.get("z", bytes(ba)))   # tpyc: ok
    print(b.pop("z", bytes(ba)))   # tpyc: ok
    # setdefault takes the same default and STORES it, so the key is present
    # on the second call and the stored value comes back
    print(b.setdefault("c", bytes(ba)))   # tpyc: ok
    print(b.setdefault("c", b"other"))    # tpyc: ok

main()
