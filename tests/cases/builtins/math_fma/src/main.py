# math.fma (fused multiply-add). CPython 3.13+; skip the cpy phase on older.
import math

def main() -> None:
    print(math.fma(2.0, 3.0, 4.0))   # 10.0
    print(math.fma(-2.0, 0.5, 1.0))  # 0.0
    print(math.fma(0.1, 0.1, 0.0))   # ~= 0.01 (fused -- tighter than naive)

main()
