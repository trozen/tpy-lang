# Empty dict with type annotation
from tpy import int32

def main() -> None:
    d: dict[str, int32] = {}
    print(d)
    print(len(d))
    d["x"] = 1
    print(d)
    print(len(d))

main()
