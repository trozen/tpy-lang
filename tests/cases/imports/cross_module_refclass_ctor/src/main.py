# Regression: cross-module direct construction of a reference-type class.
# Without the fix, codegen emits `T& sio = T()` because constructors went
# through _call_returns_cpp_ref's lvalue rule (the return type is non-value
# nominal, so the rule misclassified it). Constructors are rvalue and now
# carry FunctionInfo.is_constructor=True for codegen to short-circuit.
from tpy import Int32
import storage


def main() -> None:
    s = storage.Buffer()
    s.add("hello")
    s.add(" world")
    print(s.size())
    print(s.dump())


main()
