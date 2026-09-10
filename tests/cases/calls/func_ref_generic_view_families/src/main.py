# A generic passed as a FUNCTION REFERENCE at `T = str` / `T = bytes`. It used to
# be rejected ("generic functions use a different C++ parameter convention"),
# because a reference has no call site to materialize an owned copy at; now that
# each str/bytes type owns its C++ type the generic's slot IS the twin's own
# parameter form, so the instantiated signature matches the Fn slot as it stands.
from tpy import Fn, Int32


def identity[T](x: T) -> T:
    return x


def apply_str(f: Fn[[str], str], s: str) -> str:
    return f(s)


def apply_bytes(f: Fn[[bytes], bytes], b: bytes) -> Int32:
    return Int32(len(f(b)))


def main() -> None:
    # the subject: the generic instantiated at each view family, by reference
    print("func_ref", apply_str(identity, "hello"))  # tpyc: ok
    print("func_ref", apply_bytes(identity, b"ab"))  # tpyc: ok


main()
