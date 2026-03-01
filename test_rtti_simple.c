// Use: ./tcc -B. -I./include -c test_rtti_simple.c -o test_rtti_simple.o
#include <stdio.h>
struct Point {
    int x;
    int y;
} __attribute__((annotate("reflect", "MyPoint")));

struct Rectangle {
    struct Point top_left;
    struct Point bottom_right;
    char name[32];
} __attribute__((annotate("reflect", "MyRectangle")));

int main() {
    struct Point p;
    p.x = 10;
    p.y = 20;
    
    struct Rectangle r;
    r.top_left.x = 0;
    r.top_left.y = 0;
    r.bottom_right.x = 100;
    r.bottom_right.y = 100;
    
    printf("x+y=%d\n",p.x + p.y);
    return 0;
}