#include "native_types.hpp"

extern "C" {

struct Point g_pts[] = {{3, 4}, {5, 6}};

static struct Widget w0 = {7};

struct Widget *find_widget(int32_t k) {
    return k > 0 ? &w0 : nullptr;
}

}
