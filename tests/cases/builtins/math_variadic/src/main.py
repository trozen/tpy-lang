# Variadic math.hypot / math.gcd / math.lcm: 0, 1, 2, and 3+ arg forms.
# Exercises module-qualified variadic codegen (TpyVarargPack dispatch in
# _gen_method_call) and CPython-compatible edge cases: empty -> identity,
# single-arg -> abs, sign handling, lcm zero-shortcut, gcd coprime early-out.
import math


def main():
    # hypot: empty -> 0.0, single -> abs, multi -> sqrt(sum of squares)
    print(math.hypot())
    print(math.hypot(5.0))
    print(math.hypot(-5.0))
    print(math.hypot(3.0, 4.0))
    print(math.hypot(1.0, 2.0, 2.0))   # sqrt(1+4+4) = 3.0
    print(math.hypot(0.0, 0.0, 0.0))

    # gcd: empty -> 0, single -> abs, variadic reduces across all args
    print(math.gcd())
    print(math.gcd(12))
    print(math.gcd(-12))
    print(math.gcd(0))                 # 0 (single-zero edge)
    print(math.gcd(12, 18, 24))        # 6
    print(math.gcd(12, 18, 25))        # 1 (coprime early-out)
    print(math.gcd(0, 0, 0))           # 0

    # lcm: empty -> 1, single -> abs, zero anywhere -> 0
    print(math.lcm())
    print(math.lcm(6))
    print(math.lcm(-6))
    print(math.lcm(0))                 # 0 (single-zero edge)
    print(math.lcm(4, 6, 8))           # 24
    print(math.lcm(4, 0, 8))           # 0 (zero short-circuit)
    print(math.lcm(-4, -6, -8))        # 24 (sign always stripped)


main()
