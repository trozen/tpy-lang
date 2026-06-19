# os.environ mapping methods: update/setdefault/pop/clear/copy. Uses vars this
# program sets (distinctive prefix) so it is environment-independent. pop takes
# a required default (the single-signature v1). Byte-compared against CPython.
import os


def main():
    os.environ.update({"TPY_M_A": "1", "TPY_M_B": "2"})
    print(os.environ.get("TPY_M_A"), os.environ.get("TPY_M_B"))   # 1 2

    print(os.environ.setdefault("TPY_M_A", "x"))     # 1 (already set)
    print(os.environ.setdefault("TPY_M_C", "3"))     # 3 (newly set)
    print(os.getenv("TPY_M_C"))                       # 3

    print(os.environ.pop("TPY_M_A", "d"))            # 1
    print(os.environ.pop("TPY_M_GONE", "d"))         # d (absent -> default)
    print("TPY_M_A" in os.environ)                    # False (popped)

    d = os.environ.copy()
    print("TPY_M_B" in d, "TPY_M_C" in d)            # True True
    # copy() is an owned snapshot: mutating it leaves the live mapping alone.
    d["TPY_M_NEW"] = "z"
    print("TPY_M_NEW" not in os.environ)             # True

    # clear() unsets every var (libc + the snapshot); the mapping ends empty.
    os.environ.clear()
    print(len(os.environ), "TPY_M_B" in os.environ)  # 0 False


main()
