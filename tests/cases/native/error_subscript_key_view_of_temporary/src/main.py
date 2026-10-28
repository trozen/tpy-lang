# A subscript key is the explicit `__getitem__` call's argument: a binding that
# keeps the key view is refused a view of a temporary's storage, as the call is.
from tpy import Own, StrView, int32, readonly
from tpy.extern import cpp_template, native


@native("std::vector<std::string_view>", indirecting=True)
class Sink:
    def __init__(self) -> None: ...

    @cpp_template("({self}.push_back({0}), int32_t(0))")
    def __getitem__(self, text: StrView) -> int32: ...

    @readonly
    @cpp_template("static_cast<int32_t>({self}.size())")
    def size(self) -> int32: ...


class H:
    text: str

    def __init__(self) -> None:
        self.text = "abcdef"


def mk() -> Own[H]:
    return H()


def main() -> None:
    sink = Sink()
    # The key's temporary dies at the end of the statement; the binding keeps it.
    print(sink[mk().text])  # tpyc: error(/arg\.lends_from_temporary/)
    print(sink.size())


main()
