# Error: incompatible annotation type in list comprehension
from tpy import int32

def main() -> None:
    items: list[str] = ["a", "b"]
    bad: list[int32] = [x for x in items]  # tpyc: error(/Type mismatch.*list comprehension element/)

main()
