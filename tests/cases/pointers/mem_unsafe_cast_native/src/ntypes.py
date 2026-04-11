# Native C struct declarations for cross-module import
from tpy.extern import native
from tpy import Int32, Ptr

@native("thing_t", binding="C")
class ThingT:
    id: Int32

@native("sector_t", binding="C")
class SectorT:
    floor_height: Int32
    thinglist: Ptr[None]
