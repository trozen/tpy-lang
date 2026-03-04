# Annotation-only str decl followed by assignment -- regression guard for
# PendingStrType inference on uninit declarations.
def make_str() -> str:
    return "owned"

def main() -> None:
    # Owned source -> resolves to std::string
    x: str
    x = make_str()
    print(x)

    # View-compatible source -> resolves to std::string_view
    y: str
    y = "hello"
    print(y)

main()
