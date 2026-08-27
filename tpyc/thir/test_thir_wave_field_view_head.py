"""`for kv in self.<field>.items():` -- the single-var for head over a
FIELD-receiver dict view.

The view render is receiver-blind (`::tpy::dict_items(this->headers)`) and
the loop var's storage registration keys on the ITERABLE TYPE, so a field
receiver binds exactly as a name receiver does -- for a value-element dict
(no registration) and for a pointer-repr-element one (registered STORAGE)
alike.
"""

from .testutil import _assert_routes_byte_identical

_SRC = (
    "from tpy import Int32, Own\n"
    "class Box:\n"
    "    n: Int32\n"
    "    def __init__(self, n: Int32) -> None:\n"
    "        self.n = n\n"
    "class S:\n"
    "    headers: dict[str, str]\n"
    "    boxes: dict[str, Box]\n"
    "    def __init__(self) -> None:\n"
    "        self.headers = {}\n"
    "        self.boxes = {}\n"
    "    def merge(self) -> Own[dict[str, str]]:\n"
    "        out: dict[str, str] = {}\n"
    "        for kv in self.headers.items():\n"
    "            out[kv[0]] = kv[1]\n"
    "        return out\n"
    "    def total(self) -> Int32:\n"
    "        t = 0\n"
    "        for kv in self.boxes.items():\n"
    "            t += kv[1].n\n"
    "        return t\n"
    "def main() -> None:\n"
    "    s = S()\n"
    "    s.headers[\"a\"] = \"b\"\n"
    "    s.boxes[\"c\"] = Box(3)\n"
    "    print(s.merge()[\"a\"], s.total())\n"
    "main()\n"
)


class TestSingleVarFieldItemsHead:
    def test_routes_byte_identical(self):
        out = "".join(_assert_routes_byte_identical(_SRC))
        assert "::tpy::dict_items(this->headers)" in out
        assert "::tpy::dict_items(this->boxes)" in out
