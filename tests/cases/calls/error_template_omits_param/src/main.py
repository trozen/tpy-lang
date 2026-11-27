# A @cpp_template that never spells one of its parameters is refused: the
# argument would never be evaluated, unlike in the TPy-function twin.
from tpy import int32
from tpy.extern import cpp_template


@cpp_template("({0} + 1)")
def first(a: int32, b: int32) -> int32: ...  # tpyc: error(/never spells parameter 'b' \('\{1\}'\).*static_cast<void>\(\{1\}\)/)


def main() -> None:
    print(first(1, 2))


main()
