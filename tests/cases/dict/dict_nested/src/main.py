# Nested dict: annotation propagates to inner dict literals
from tpy import Int32

def main() -> None:
    d: dict[str, dict[str, Int32]] = {"inner": {"a": 1, "b": 2}}
    print(d["inner"]["a"])
    print(d["inner"]["b"])

    d2: dict[str, dict[str, str]] = {"x": {"k": "v"}}
    print(d2["x"]["k"])
main()
