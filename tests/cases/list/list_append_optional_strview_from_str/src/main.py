# Reverse direction of list_append_optional_str_rvalue: Optional[str] source
# flowing into `list[Optional[StrView]]` elements (Own[Optional[StrView]]
# target). The source's C++ type is std::optional<std::string> (non-ARG
# storage) or std::optional<std::string_view> (ARG) depending on site; the
# target is std::optional<std::string_view>. The `optional_str_to_strview`
# coercion materializes when target is Own-wrapped, so the view is
# synthesized from the live std::string.
#
# This test pins down the codegen path. Note that a real program storing
# Optional[StrView] in a container must keep the backing strings alive for
# the container's lifetime -- here main() holds the sources as locals until
# print().
from tpy import StrView
from typing import Optional


def maybe() -> Optional[str]:
    return "hello"


def main() -> None:
    src1: Optional[str] = "one"
    src2: Optional[str] = None
    src3 = maybe()
    items: list[Optional[StrView]] = []
    items.append(src1)
    items.append(src2)
    items.append(src3)
    print(items)


main()
