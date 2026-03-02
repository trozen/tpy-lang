# Empty dict with type annotation
from tpy import Int32

def main() -> None:
    d: dict[str, Int32] = {}
    print(d)
    print(len(d))
    d["x"] = 1
    print(d)
    print(len(d))

main()
