# Storing a str (string_view at parameter position) in Any owns a copy --
# the Any cell holds an std::string, not the borrowed view. The view's
# original buffer can die without invalidating the Any.

from typing import Any


def take(s: str) -> Any:
    a: Any = s   # storage upgrade: StrView -> std::string
    return a


def main() -> None:
    a = take("ephemeral")
    # The string literal "ephemeral" was a string_view at the call boundary;
    # `a` owns its own std::string copy regardless.
    print("stored")


main()
