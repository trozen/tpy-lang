# Final with primitive type constructor calls: Float32(lit), Int64(FINAL), etc.
from typing import Final
from tpy import Int32, Int64, Float32

X: Final[Float32] = Float32(0.5)
Y: Final[Int64] = Int64(42)
BASE: Final[Int32] = 10
Z: Final[Int64] = Int64(BASE)

def main() -> None:
    print(X)
    print(Y)
    print(Z)

main()
