# Kwargs that skip defaulted params: f(1, c=3) where b has default
from tpy import int32

def f(a: int, b: int = 10, c: int = 20) -> None:
    print(f"a={a} b={b} c={c}")

def g(x: str, y: str = "default_y", z: str = "default_z") -> None:
    print(f"x={x} y={y} z={z}")

# Also test with int32 to cover the fixed-int default path
def h(a: int32, b: int32 = int32(100)) -> None:
    print(f"a={a} b={b}")

def main() -> None:
    f(1, c=3)
    f(1)
    f(1, 2, c=3)
    g("hello", z="world")
    h(int32(5), b=int32(9))

main()
