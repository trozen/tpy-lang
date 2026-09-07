# A closure whose name SHADOWS a builtin. CPython binds `abs` to the nested
# def for the whole function, but TPy resolves the builtin first -- the reject
# is what keeps that divergence loud instead of silently calling the builtin.
from tpy import Int32


def main() -> None:
    def abs(x: Int32) -> Int32:
        return x + 100

    # CPython calls the closure here and prints 97; TPy reaches the builtin.
    print(abs(-3))  # tpyc: error(/stmt\.expr_stmt:call\.closure_import_shadow/)


main()
