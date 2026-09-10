# Test open_text() and open_binary() explicit non-overloaded alternatives
from tpy import open_text, open_binary

def main() -> None:
    tpath = "tpy_test_open_text_binary.txt"
    bpath = "tpy_test_open_text_binary.bin"

    # open_text with explicit mode
    w = open_text(tpath, "w")
    w.write("hello")
    w.close()

    # open_text with default mode (read)
    r = open_text(tpath)
    print(r.read())
    r.close()

    # open_binary with explicit mode
    bw = open_binary(bpath, "wb")
    bw.write(b"\x01\x02\x03")
    bw.close()

    # open_binary with default mode (rb)
    br = open_binary(bpath)
    data = br.read()
    br.close()
    print(len(data))
    print(data[0], data[1], data[2])

    # Variable mode works (no Literal dispatch needed)
    mode = "w"
    w2 = open_text(tpath, mode)
    w2.write("world")
    w2.close()

    r2 = open_text(tpath)
    print(r2.read())
    r2.close()

main()
