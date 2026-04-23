# list[Optional[str]] with Optional[StrView] rvalue sources through append,
# insert, __setitem__. Regression: the `optional_strview_to_str` coercion
# used to be identity at ARG context on the assumption that both sides lower
# to `std::optional<std::string_view>`. That's correct for plain
# Optional[str] params but wrong for Own[Optional[str]] targets (container
# element slots), where the C++ target is `std::optional<std::string>`.
# The ARG-only gate also meant ASSIGN/INIT/RETURN contexts hit a hard type
# mismatch for the same conversion.

from tpy import StrView
from typing import Optional

def maybe_prefix(subject: str, keep: bool) -> Optional[StrView]:
    if keep:
        return subject[:5]
    return None

def main() -> None:
    items: list[Optional[str]] = []
    subject = "hello world"
    # maybe_prefix() defeats narrowing, so `local` stays Optional[StrView].
    local = maybe_prefix(subject, True)
    items.append(local)
    items.append(maybe_prefix(subject, True))
    items.append(maybe_prefix(subject, False))
    items.insert(1, maybe_prefix(subject, True))
    items[0] = maybe_prefix(subject, False)
    print(items)

main()
