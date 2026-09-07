# An explicit `str.__iter__()` as a PRINT argument: the widened
# view-iterator row is storage-sink only, and a print argument is not one.
# TPy rejects `print(chars.__iter__())` today.
def main() -> None:
    chars: str = "hi"
    print(chars.__iter__())  # tpyc: error(/stmt\.expr_stmt:method\.view\.ret_type/)


main()
