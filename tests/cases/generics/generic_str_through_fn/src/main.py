# Regression: generic U over str composed with Fn[..., U] used to auto-downgrade
# U to StrView. The lambda's body produced an owned std::string, the lambda's
# declared return became std::string_view, and the implicit conversion yielded
# a dangling view -- silent miscompile printing garbled bytes. Fixed by removing
# the auto-downgrade so U stays as str (storage form std::string) throughout.
from tpy import Own, copy, Fn

def apply[U](f: Fn[[U], U], init: U) -> Own[U]:
    return copy(f(init))

def reduce2[U](f: Fn[[U, U], U], a: U, b: U) -> Own[U]:
    return copy(f(a, b))

def main() -> None:
    # Single-arg Fn[[U], U] -- the canonical bug case
    s1 = apply(lambda s: s + "!", "hello")
    print(s1)

    # Two-arg Fn[[U, U], U] -- the reduce-shape variant
    s2 = reduce2(lambda a, b: a + b, "foo", "bar")
    print(s2)

    # int still works the same way
    n1 = apply(lambda x: x + 1, 41)
    print(n1)

    n2 = reduce2(lambda a, b: a + b, 10, 20)
    print(n2)

main()
