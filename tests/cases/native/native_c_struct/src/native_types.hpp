#pragma once
#include <cstdint>

struct Point {
    int32_t x;
    int32_t y;
    int32_t manhattan() const;
};

struct Rect {
    int32_t x;
    int32_t y;
    int32_t w;
    int32_t h;
    int32_t area() const;
};

#ifdef __cplusplus
extern "C" {
#endif

int32_t point_sum(Point* p);
int32_t rect_area(Rect* r);

#ifdef __cplusplus
}
#endif
