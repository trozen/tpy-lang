#include "native_types.hpp"

extern "C" int32_t rect_area(c_rect* r) {
    return r->w * r->h;
}
