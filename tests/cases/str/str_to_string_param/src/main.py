# str -> String coercion at function-arg position. Regression: `str` params
# lower to std::string_view in C++ while `String` params lower to
# const std::string&. The `str_to_string` coercion used to be identity at
# every context, which fails to bind string_view to const std::string&.
from tpy import String


def take_string(s: String) -> String:
    return s


def forward(s: str) -> String:
    # s is a str param (C++: std::string_view). take_string expects String
    # (C++: const std::string&). Requires materialization.
    return take_string(s)


def main() -> None:
    out = forward("hello from str")
    print(out)


main()
