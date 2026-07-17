# Construct a native (builtin) exception into a var and raise it via `raise e`
# (the expr form) -- exercises native-exception ctor-into-var + raise <expr>
# with no surrounding try, so the whole body routes through THIR.
def main() -> None:
    e = OSError(2, "No such file or directory")
    raise e


main()
