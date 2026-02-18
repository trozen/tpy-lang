#pragma once
#include <cstdint>

struct c_rect {
    int32_t x;
    int32_t y;
    int32_t w;
    int32_t h;
};

#ifdef __cplusplus
extern "C" {
#endif

int32_t rect_area(c_rect* r);

#ifdef __cplusplus
}
#endif
