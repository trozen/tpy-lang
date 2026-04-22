# StrView <-> str coercion must lift through Optional at arg position.
# Both `str | None` and `StrView | None` params lower to the same C++ type
# (`std::optional<std::string_view>`), so either should accept the other.
from tpy import StrView

def takes_str(s: str) -> None:
    print(s)

def takes_str_opt(s: str | None) -> None:
    if s is None:
        print("(none)")
    else:
        print(s)

def takes_strview_opt(s: StrView | None) -> None:
    if s is None:
        print("(none)")
    else:
        print(s)

def returns_view() -> StrView:
    return StrView("view")

def returns_view_opt() -> StrView | None:
    return StrView("opt-view")

def returns_view_none() -> StrView | None:
    return None

def returns_str_opt() -> str | None:
    return "opt-str"

def main() -> None:
    # Non-optional baseline -- StrView -> str arg works today.
    takes_str(returns_view())                              # tpyc: ok
    # New: Optional[StrView] -> Optional[str] arg.
    takes_str_opt(returns_view_opt())                      # tpyc: ok
    takes_str_opt(returns_view_none())                     # tpyc: ok
    # New: Optional[str] -> Optional[StrView] arg (reverse direction).
    takes_strview_opt(returns_str_opt())                   # tpyc: ok

main()
