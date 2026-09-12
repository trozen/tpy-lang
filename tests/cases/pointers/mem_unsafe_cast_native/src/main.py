# unsafe_cast with native types across modules -- must use C type names
from tpy import Ptr, int32, take_ptr
from tpy.unsafe import unsafe_cast
from ntypes import ThingT, SectorT

def get_thing(sec: Ptr[SectorT]) -> Ptr[ThingT]:
    return unsafe_cast[ThingT](sec.thinglist)

def main() -> None:
    thing = ThingT(int32(42))
    sec = SectorT(int32(100), unsafe_cast[None](take_ptr(thing)))
    p: Ptr[ThingT] = get_thing(take_ptr(sec))
    print(p.id)

main()
