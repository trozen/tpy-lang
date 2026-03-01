# Kwargs that skip defaulted params: f(1, c=3) where b has default
from tpy import Int32

def f(a: int, b: int = 10, c: int = 20) -> None:
    print(f"a={a} b={b} c={c}")

def g(x: str, y: str = "default_y", z: str = "default_z") -> None:
    print(f"x={x} y={y} z={z}")

# Also test with Int32 to cover the fixed-int default path
def h(a: Int32, b: Int32 = Int32(100)) -> None:
    print(f"a={a} b={b}")

def main() -> None:
    f(1, c=3)
    f(1)
    f(1, 2, c=3)
    g("hello", z="world")
    h(Int32(5), b=Int32(9))

main()
