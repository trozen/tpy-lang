# Final with primitive type constructor calls: float32(lit), int64(FINAL), etc.
from typing import Final
from tpy import int32, int64, float32

X: Final[float32] = float32(0.5)
Y: Final[int64] = int64(42)
BASE: Final[int32] = 10
Z: Final[int64] = int64(BASE)

def main() -> None:
    print(X)
    print(Y)
    print(Z)

main()
