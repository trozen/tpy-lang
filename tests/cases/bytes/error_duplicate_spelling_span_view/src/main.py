# The one union pair still sharing ONE C++ spelling: BytesView and
# Span[readonly[UInt8]] both render std::span<const uint8_t>, so
# std::holds_alternative<T> / std::get<T> are ill-formed on the variant and the
# widened member class keeps the union out, rejecting at a located tag rather
# than at an unlocated g++ error. The bytes/str family closed when each type got
# its own C++ type (bytes/distinct_spelling_union). BUGS.md#duplicate-cpp-spelling-union
from tpy import Int32, UInt8, BytesView, Span, readonly


def span_or_bytesview(u: Span[readonly[UInt8]] | BytesView) -> Int32:
    if isinstance(u, BytesView):  # tpyc: error(/cond.call/)
        return len(u)
    return 0


def main() -> None:
    print(1)


main()
