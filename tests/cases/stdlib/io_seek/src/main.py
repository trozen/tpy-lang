# io.SEEK_SET/CUR/END constants + use with StringIO.seek. Text streams only
# allow zero-offset CUR/END seeks (CPython rule), so the constants are exercised
# via absolute SEEK_SET offsets and seek(0, SEEK_END). Byte-compared to CPython.
import io


def main():
    print(io.SEEK_SET, io.SEEK_CUR, io.SEEK_END)   # 0 1 2
    s = io.StringIO("hello world")
    s.seek(6, io.SEEK_SET)
    print(s.read())                                 # world
    s.seek(0, io.SEEK_END)
    print(s.tell())                                 # 11
    s.seek(0, io.SEEK_SET)
    print(s.read(5))                                # hello
    s.seek(0, io.SEEK_CUR)                          # text streams allow 0-offset CUR
    print(s.read())                                 # " world"


main()
