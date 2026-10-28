# A binding that keeps a VIEW argument (`push_back` of a `std::string_view`) is
# refused a view of a temporary's storage (more shapes: test_call_retention.py).
from tpy import Own, StrView, int32, readonly
from tpy.extern import cpp_template, native


@native("std::vector<std::string_view>", indirecting=True)
class Sink:
    def __init__(self) -> None: ...

    @native("push_back")
    def stash(self, text: StrView) -> None: ...

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
    # The binding keeps the view it is handed; the temporary dies here.
    sink.stash(mk().text)  # tpyc: error(/arg\.lends_from_temporary/)
    print(sink.size())


main()
