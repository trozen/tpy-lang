# `d.setdefault(key, default)`: the key is a lookup form, so a literal or a
# param passes as a view and the runtime builds the stored key only on a miss.
# The `str`-valued section is the DEFAULT's form: setdefault is the sibling
# that STORES its default, so a literal, a `String` lvalue and a `char` each
# have to become the dict's `str` on the miss path and be ignored on the hit.
from tpy import String, char, int32

def main() -> None:
    d: dict[str, int32] = {"a": 1, "b": 2}
    print(d.setdefault("a", 99))   # 1 (exists)
    print(d.setdefault("c", 42))   # 42 (inserted)
    print(d["c"])                    # 42
    print(len(d))                    # 3

    s: dict[str, str] = {"a": "alpha"}
    lv = String("lvalue")
    print("lit hit", s.setdefault("a", "lit"))        # tpyc: ok
    print("lit miss", s.setdefault("l", "lit"))       # tpyc: ok
    print("String hit", s.setdefault("a", lv))        # tpyc: ok
    print("String miss", s.setdefault("s", lv))       # tpyc: ok
    print("char hit", s.setdefault("a", char("c")))   # tpyc: ok
    print("char miss", s.setdefault("h", char("c")))  # tpyc: ok
    print("stored", s["l"], s["s"], s["h"], len(s))

main()
