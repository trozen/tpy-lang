# macro_api._is_static_str should recognise Name references that resolve to
# module-level Final[str] constants (local or imported), not just literals and
# ternaries of literals. Each f-string part is tagged [static] or [dynamic] by
# the label macro; printed output makes the classification observable.
from typing import Final
from label_macro import label
from const_mod import VERSION

NAME: Final[str] = "tpy"

def main() -> None:
    dynamic = "world"
    # Literal -> static
    # NAME (local Final[str]) -> static
    # VERSION (imported Final[str]) -> static
    # dynamic (non-final) -> dynamic
    label(f"hello {NAME} v{VERSION} ({dynamic})")

main()
