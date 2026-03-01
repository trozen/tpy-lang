# Keyword arguments in different order than parameters

def point_str(x: int, y: int, z: int) -> str:
    return f"({x}, {y}, {z})"

def main() -> None:
    print(point_str(z=3, x=1, y=2))
    print(point_str(y=20, z=30, x=10))

main()
