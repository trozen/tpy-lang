# Deleting a slice is not supported yet (BUGS.md#del-slice-rejected): the
# rejection says so in plain words, and on bytearray it wins over the
# missing-__delitem__ message.
def main() -> None:
    buf = bytearray(b"abcdef")
    i = 2
    del buf[:i]  # tpyc: error(/deleting a slice \('del xs\[a:b\]'\) is not yet supported/)
    print(buf)


main()
