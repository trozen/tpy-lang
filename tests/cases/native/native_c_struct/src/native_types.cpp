#include "native_types.hpp"

int32_t Point::manhattan() const {
    return x + y;
}

int32_t Rect::area() const {
    return w * h;
}

extern "C" int32_t point_sum(Point* p) {
    return p->x + p->y;
}

extern "C" int32_t rect_area(Rect* r) {
    return r->w * r->h;
}
