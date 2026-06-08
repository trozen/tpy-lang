# Same incompatible reassignment as error_str_reassign_int, but with an
# explicit str annotation -- the annotated decl still lowers to the view
# family, so the reassignment compat check must fire here too.
def main() -> None:
    s: str = "asd"
    s = 5  # tpyc: error(/Type mismatch in reassignment to 's': expected str/)
    print(s)

main()
