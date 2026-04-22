# float("nan") / float("inf") / float("-inf") -- special string tokens fold
# to constexpr std::numeric_limits at codegen so Final[float] initializers
# work without going through the non-constexpr tpy::float_from_str runtime.
# Case-insensitive; whitespace trimmed; also accepts "infinity", "+inf", etc.
from typing import Final
import math
import builtins

# Final[float] initializer (would constexpr-panic without the fold).
MY_NAN: Final[float] = float("nan")
MY_INF: Final[float] = float("inf")
MY_NEG_INF: Final[float] = float("-inf")


def main() -> None:
    # Final constants behave like their math.* counterparts.
    print(math.isnan(MY_NAN))
    print(math.isinf(MY_INF))
    print(math.isinf(MY_NEG_INF))
    print(MY_INF > 0.0)
    print(MY_NEG_INF < 0.0)

    # Runtime float(literal-string) also folds (non-Final context).
    print(math.isnan(float("nan")))
    print(math.isinf(float("inf")))
    print(math.isinf(float("-inf")))

    # Accepted CPython aliases: "infinity", explicit-+, case-insensitive,
    # surrounding whitespace.
    print(math.isinf(float("infinity")))
    print(math.isinf(float("+inf")))
    print(math.isinf(float("+infinity")))
    print(math.isinf(float("-infinity")))
    print(math.isnan(float("NaN")))
    print(math.isnan(float("NAN")))
    print(math.isinf(float("INF")))
    print(math.isinf(float("  inf  ")))

    # Signed NaN: -quiet_NaN() flips the IEEE 754 sign bit (matches CPython;
    # both are still NaN, self-inequality, print as "nan"). +nan is identity.
    print(math.isnan(float("-nan")))
    print(math.isnan(float("+nan")))

    # Module-qualified builtins.float(str_literal) -- routes through the
    # builtin_module_call dispatch in codegen and triggers the same fold.
    print(math.isinf(builtins.float("inf")))
    print(math.isnan(builtins.float("nan")))


def test_shadow() -> None:
    # Regression: peephole must not fire when user shadows float(). Before the
    # resolved_function_info gate, codegen matched func_name=="float"
    # unconditionally and emitted numeric_limits<double> into a non-float
    # context.
    def float(s: str) -> int:
        return len(s)
    x = float("nan")
    print(x)  # 3


main()
test_shadow()
