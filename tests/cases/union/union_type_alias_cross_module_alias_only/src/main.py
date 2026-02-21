# Test importing only the alias (not member types) from another module.
# Member record types (Circle, Rect) should be implicitly imported for codegen.
from tpy import Int32
from shapes import Shape


def describe(s: Shape) -> str:
    return "shape"


def main() -> None:
    print("ok")

main()
