# Container method calls on a list[int32] param, routed through THIR
# (--thir-codegen, byte-identical to the AST path): statement-position mutation
# calls across all three emit arms (@native member `xs.push_back(v)`, @native
# free function `::tpy::pop_back(xs)`, @cpp_template `std::stable_sort(...)`),
# plus value-position `xs.pop()` as a decl init, a binop operand, and a print
# arg. The caller observes every mutation through the shared list (reference
# semantics forced).
from tpy import int32


def grow(xs: list[int32], n: int32) -> None:
    xs.append(5)
    xs.append(n)
    xs.insert(0, 7)
    xs.pop(0)
    xs.reverse()
    xs.sort()
    xs.remove(5)


def take_two(xs: list[int32]) -> int32:
    a = xs.pop()
    return a + xs.pop()


def wipe(xs: list[int32]) -> None:
    xs.clear()


def show_last(xs: list[int32]) -> None:
    print(xs.pop())


def main() -> None:
    xs = [3, 1]
    grow(xs, 9)
    show_last(xs)
    print(len(xs))
    for x in xs:
        print(x)
    print(take_two(xs))
    print(len(xs))
    wipe(xs)
    print(len(xs))


main()
