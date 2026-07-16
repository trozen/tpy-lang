# Renaming a cross-module plain-union alias on import currently fails to
# resolve: `from shapes import Shape as MyShape` maps `MyShape` back to the
# defining module's `Shape`, which the local-decl annotation resolver then
# cannot find (see BUGS.md). This error-pin also guards a latent codegen
# divergence lurking behind that bug: were the alias to resolve, the
# pointer-variant UNION_RVALUE storage slot (`MyShape __slot_N = ...`) would
# expose a display-vs-alias name mismatch between the AST path (union_alias_names
# -> the importing module's "MyShape") and THIR (union_display_names -> the
# defining module's "Shape"). When this rejection is lifted, the storage-slot
# name divergence must be resolved together.
from tpy import Int32
from shapes import Circle, Rect, Shape as MyShape


def describe(s: MyShape) -> str:
    if isinstance(s, Circle):
        return "circle"
    assert isinstance(s, Rect)
    return "rect"


def main() -> None:
    c: MyShape = Circle(Int32(10))  # tpyc: error(/Unknown type: Shape/)
    print(describe(c))

main()
