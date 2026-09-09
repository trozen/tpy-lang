# Fused results and catchable exceptions follow math.fma in CPython 3.13+.
# This case has a version exclusion because the baseline CPython lacks fma.
import math

def main() -> None:
    print(math.fma(2.0, 3.0, 4.0))   # 10.0
    print(math.fma(-2.0, 0.5, 1.0))  # 0.0
    print(math.fma(0.1, 0.1, 0.0))   # ~= 0.01 (fused -- tighter than naive)

    # An intermediate multiply overflows, but the fused final result is finite.
    maximum = 1.7976931348623157e308
    print("fusion", math.fma(maximum, 2.0, -maximum) == maximum)  # tpyc: ok
    # The exact product's low bits survive when the rounded product is one.
    print("low bits", math.fma(1.0000000074505806, 0.9999999925494194, -1.0) == -5.551115123125783e-17)  # tpyc: ok

    # An input NaN takes precedence over the otherwise invalid zero * infinity.
    print("nan third", math.isnan(math.fma(0.0, math.inf, math.nan)))  # tpyc: ok
    print("nan third swapped", math.isnan(math.fma(math.inf, 0.0, math.nan)))  # tpyc: ok
    print("nan first", math.isnan(math.fma(math.nan, math.inf, -math.inf)))  # tpyc: ok
    print("nan second", math.isnan(math.fma(math.inf, math.nan, -math.inf)))  # tpyc: ok
    print("infinity", math.fma(math.inf, 2.0, math.inf) == math.inf)  # tpyc: ok
    print("negative infinity", math.fma(-math.inf, 2.0, -math.inf) == -math.inf)  # tpyc: ok

    # Underflow and signed zero remain valid results.
    underflow = math.fma(-5e-324, 0.5, -0.0)  # tpyc: ok
    print("underflow", underflow == 0.0, math.copysign(1.0, underflow) == -1.0)
    negative_zero = math.fma(-0.0, 2.0, -0.0)  # tpyc: ok
    positive_zero = math.fma(-0.0, 2.0, 0.0)  # tpyc: ok
    print("zero signs", math.copysign(1.0, negative_zero), math.copysign(1.0, positive_zero))

    # NaN without a NaN input is an invalid operation, caught as ValueError.
    try:
        math.fma(0.0, math.inf, 1.0)  # tpyc: ok
        print("invalid missed")
    except ValueError:
        print("invalid ValueError")
    try:
        math.fma(math.inf, 1.0, -math.inf)  # tpyc: ok
        print("opposed missed")
    except ValueError:
        print("opposed ValueError")

    # All inputs are finite, so either sign of infinite result is overflow.
    try:
        math.fma(maximum, 2.0, 0.0)  # tpyc: ok
        print("overflow missed")
    except OverflowError:
        print("overflow OverflowError")
    try:
        math.fma(-maximum, 2.0, 0.0)  # tpyc: ok
        print("negative overflow missed")
    except OverflowError:
        print("negative overflow OverflowError")

main()
