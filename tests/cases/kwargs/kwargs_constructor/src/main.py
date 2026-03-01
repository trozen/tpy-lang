# Keyword arguments in record constructors

class Point:
    def __init__(self, x: int, y: int) -> None:
        self.x = x
        self.y = y

class Config:
    def __init__(self, host: str, port: int = 8080, verbose: bool = False) -> None:
        self.host = host
        self.port = port
        self.verbose = verbose

def main() -> None:
    p1 = Point(y=20, x=10)
    print(f"p1: ({p1.x}, {p1.y})")

    p2 = Point(1, y=2)
    print(f"p2: ({p2.x}, {p2.y})")

    c1 = Config("localhost", verbose=True)
    print(f"c1: {c1.host}:{c1.port} verbose={c1.verbose}")

    c2 = Config(host="example.com", port=9090)
    print(f"c2: {c2.host}:{c2.port} verbose={c2.verbose}")

main()
