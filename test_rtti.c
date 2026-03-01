// Test RTTI functionality
#include <stdio.h>

__attribute__((annotation("reflect")))
struct Point {
    int x;
    int y;
};

__attribute__((annotation("reflect")))
struct Rectangle {
    struct Point top_left;
    struct Point bottom_right;
    char name[32];
};

int main() {
    struct Point p = {10, 20};
    struct Rectangle r = {{0, 0}, {100, 100}, "MyRect"};
    
    printf("Point: x=%d, y=%d\n", p.x, p.y);
    printf("Rectangle: (%d,%d) - (%d,%d), name=%s\n", 
           r.top_left.x, r.top_left.y, 
           r.bottom_right.x, r.bottom_right.y, 
           r.name);
    
    printf("\nRTTI test passed!\n");
    return 0;
}