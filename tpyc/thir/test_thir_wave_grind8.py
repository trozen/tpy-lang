"""Wave 8 of the grind loop: the short-name collision override at ctor
shape resolution.

With two records sharing a short name (`from screen import Point` beside
`from world import Point as WorldPoint`), the registry's short-name
lookup is last-write-wins while sema resolves the TYPE qname-first. The
AST emits from the type; `_ctor_shape_ok` now validates against the same
type-resolved record instead of rejecting the override (the THIRCtorCall
lowering already read get_record_for_type).
"""

from ..codegen_cpp.context import CodeGenOptions
from .testutil import (
    _assert_routes_byte_identical,
)


class TestDupShortNameCtors:
    def test_both_colliding_points_route_qualified(self, tmp_path):
        # BOTH colliding records in one program (the survey rule): each
        # ctor spells its own module's qualification, and both members of
        # the shared union lift with fully qualified spellings.
        (tmp_path / "screen.py").write_text(
            "from tpy import Int32\n"
            "class Point:\n"
            "    x: Int32\n"
            "    def __init__(self, x: Int32) -> None:\n"
            "        self.x = x\n"
        )
        (tmp_path / "world.py").write_text(
            "from tpy import Int32\n"
            "class Point:\n"
            "    lat: Int32\n"
            "    def __init__(self, lat: Int32) -> None:\n"
            "        self.lat = lat\n"
        )
        src = (
            "from tpy import Int32\n"
            "from screen import Point\n"
            "from world import Point as WorldPoint\n"
            "def shift(p: Point | WorldPoint) -> None:\n"
            "    if isinstance(p, Point):\n"
            "        p.x += 1\n"
            "    if isinstance(p, WorldPoint):\n"
            "        p.lat += 1\n"
            "def main() -> None:\n"
            "    s = Point(10)\n"
            "    w = WorldPoint(20)\n"
            "    shift(s)\n"
            "    shift(w)\n"
            "    print(s.x, w.lat)\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(
            src, extra_lib_dirs=[tmp_path])
        assert "::tpyapp::screen::Point s = ::tpyapp::screen::Point(10);" in cpp
        assert "::tpyapp::world::Point w = ::tpyapp::world::Point(20);" in cpp
        assert ("std::variant<::tpyapp::screen::Point*, "
                "::tpyapp::world::Point*>{&(s)}") in cpp
