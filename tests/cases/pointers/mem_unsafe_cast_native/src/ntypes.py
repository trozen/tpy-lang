# Native C struct declarations for cross-module import
from tpy.extern import native
from tpy import int32, Ptr

@native("thing_t", binding="C")
class ThingT:
    id: int32

@native("sector_t", binding="C")
class SectorT:
    floor_height: int32
    thinglist: Ptr[None]
