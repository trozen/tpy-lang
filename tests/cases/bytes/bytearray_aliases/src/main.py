# bytearray is a reference type: binding a local or reading a field aliases the
# buffer (no deep copy), so mutation through the alias is visible, like CPython.
class Holder:
    def __init__(self, data: bytearray):
        # bytearray is a reference type: storing into a field copies (storage
        # form), warned like list/dict/set.
        self.data = data  # tpyc: warning(/copies bytearray into field/)

def main():
    ba = bytearray(b"ab")
    x = ba              # local bind aliases the buffer
    x.append(99)
    print(len(ba))      # 3 -- mutation through x is visible on ba

    h = Holder(bytearray(b"xy"))
    y = h.data          # field read aliases the field's buffer
    y.append(7)
    print(len(h.data))  # 3 -- mutation through y is visible on the field

main()
