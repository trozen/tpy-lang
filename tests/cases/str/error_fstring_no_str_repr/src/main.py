# Test error: f-string interpolation on a type without __str__ or __repr__
class Bar:
    x: int

def main() -> None:
    b: Bar = Bar(1)
    s: str = f"{b}"  # tpyc: error(/no __str__ or __repr__ method/)

main()
