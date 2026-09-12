# Python `%` on floats uses floor semantics (sign-of-divisor): the result
# always has the sign of the divisor. C's std::fmod uses truncation
# (sign-of-dividend). Mixed-sign cases are where the two diverge.
from tpy import float32


def main() -> None:
    # Same-sign: no behavior change vs C fmod.
    print(7.5 % 2.5)        # 0.0
    print(7.0 % 3.0)        # 1.0
    print(-7.0 % -3.0)      # -1.0

    # Mixed-sign: Python's floor semantics, not C's truncation.
    print(-1.5 % 2.5)       # 1.0  (C fmod: -1.5)
    print(1.5 % -2.5)       # -1.0 (C fmod:  1.5)
    print(-7.0 % 3.0)       # 2.0  (C fmod: -1.0)
    print(7.0 % -3.0)       # -2.0 (C fmod:  1.0)

    # Zero-result cases adopt the divisor's sign (matches CPython).
    print(-0.0 % 3.0)       # 0.0   (raw fmod gives -0.0)
    print(0.0 % -3.0)       # -0.0  (raw fmod gives  0.0)

    # float32 path takes the fmod_f32 branch (both sign permutations).
    af: float32 = float32(-7.0)
    bf: float32 = float32(3.0)
    print(af % bf)          # 2.0
    cf: float32 = float32(7.0)
    df: float32 = float32(-3.0)
    print(cf % df)          # -2.0


main()
