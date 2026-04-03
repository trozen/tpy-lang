# str()/f-string for containers holding records
class Point:
    x: int
    y: int
    def __init__(self, x: int, y: int) -> None:
        self.x = x
        self.y = y
    def __repr__(self) -> str:
        return f"Point(x={self.x}, y={self.y})"

def main() -> None:
    pts: list[Point] = [Point(1, 2), Point(3, 4)]
    print(str(pts))
    print(f"{pts}")

    t: tuple[Point, int] = (Point(5, 6), 7)
    print(str(t))

    d: dict[str, Point] = {"origin": Point(0, 0)}
    print(str(d))

main()
