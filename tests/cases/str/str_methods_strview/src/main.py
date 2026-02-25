# Test StrView string methods (same methods work on string_view)
from tpy import StrView

def test_methods(s: StrView) -> None:
    print("strip:", s.strip())
    print("upper:", s.upper())
    print("lower:", s.lower())
    print("find:", s.find("ll"))
    print("startswith:", s.startswith("he"))
    print("endswith:", s.endswith("lo"))
    print("count:", s.count("l"))
    print("replace:", s.replace("l", "r"))
    print("isalpha:", s.isalpha())

def main() -> None:
    sv: StrView = "hello"
    test_methods(sv)

main()
