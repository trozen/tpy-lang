# `tpy.String` renders its own C++ type instead of sharing `std::string` with
# `str`. A derived type stops matching an exact-type test and loses an overload
# found by template argument deduction, so every way a String reaches the
# printing / repr / hashing surface is pinned here: each section is a position
# where the shared spelling used to answer and a bare derived type would not.
# The `Optional[String]` repr arm has its row in the runtime too, but no TPy
# spelling reaches it: a `String | None` local rejects at `decl.slot_type` and
# the param form at `call.native_arg.optptr`, both identical on master.
from tpy import Int32, String


def repr_and_str(s: String) -> None:
    # repr must still quote (the formattable fallback would print it raw)
    print("repr", repr(s), str(s), len(s))


def fstring(s: String) -> None:
    # an f-string operand goes through std::format, which needs a formatter
    print("fstring", f"[{s}]")


def container_element(s: String) -> None:
    # a list element reprs through repr_of, not through operator<<
    xs: list[String] = [s, String("b")]
    print("element", xs)


def show[T](v: T) -> None:
    # ValuePrinter inside a generic body: a String is a range, so without its
    # own arm it would print as a char list
    print("generic", v)


def dict_key(s: String) -> None:
    # a dict keyed on String, probed with a `str` key: the lookup must compare
    # and hash in the stored type's domain rather than building an element
    d: dict[String, Int32] = {}
    d[s] = 1
    print("dictkey", "a" in d, d["a"], len(d))


def main() -> None:
    s = String("a")
    repr_and_str(s)
    fstring(s)
    container_element(s)
    show(s)
    dict_key(s)


main()
