# Post-access Ptr[T] non-null narrowing must flush at statement boundaries,
# not mid-expression. C++ leaves sub-expression evaluation order unspecified
# for arithmetic, and only sequences RHS-before-LHS for whole assignments --
# eliding a sibling access inside one expression would risk UB if the unchecked
# side ran first and the pointer was null.
from tpy import Ptr, Int32

class A:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

def cross_statement(p: Ptr[A]) -> None:
    # First statement checks; subsequent statements elide.
    print(p.x)
    print(p.y)
    print(p.x + p.y)

def within_expression(p: Ptr[A]) -> None:
    # First use of p across all statements in this function:
    # both operands must check -- evaluation order unspecified.
    print(p.x + p.y)
    # Now p is proven non-null; subsequent accesses elide.
    print(p.x + p.y)

def assign_first(p: Ptr[A]) -> None:
    # RHS evaluated before LHS, but it's the first use overall:
    # both sides must check. After this statement, p is non-null.
    p.x = p.y + 1
    p.x = p.y + 2

def main() -> None:
    a = A(10, 20)
    p: Ptr[A] = a
    cross_statement(p)
    within_expression(p)
    assign_first(p)
    print(a.x, a.y)

main()
