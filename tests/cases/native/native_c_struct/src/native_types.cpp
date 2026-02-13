#include "native_types.hpp"

extern "C" int32_t point_sum(Point* p) {
    return p->x + p->y;
}

extern "C" int32_t rect_area(Rect* r) {
    return r->w * r->h;
}
