#pragma once
#include <cstdint>

struct Widget {
    int32_t n;
};

struct Point {
    int32_t x;
    int32_t y;
};

#ifdef __cplusplus
extern "C" {
#endif

extern struct Point g_pts[];
struct Widget *find_widget(int32_t k);

#ifdef __cplusplus
}
#endif
