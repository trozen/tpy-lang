# Test coercions between str, String, StrView, and Char
from tpy import String, StrView, Char

def take_str(s: str) -> None:
    print(s)

def take_string(s: String) -> None:
    print(s)

def take_strview(s: StrView) -> None:
    print(s)

def main() -> None:
    # String -> str (identity, both std::string)
    s1: String = String("hello")
    take_str(s1)  # hello

    # str -> String (identity)
    s2: str = "world"
    take_string(s2)  # world

    # String -> StrView (safe implicit)
    take_strview(s1)  # hello

    # str -> StrView (safe implicit)
    take_strview(s2)  # world

    # StrView -> String (allocates)
    sv: StrView = StrView("view")
    take_string(sv)  # view

    # StrView -> str (allocates)
    take_str(sv)  # view

    # Char -> str
    c: Char = "X"
    take_str(c)  # X

    # Char -> String
    take_string(c)  # X

main()
