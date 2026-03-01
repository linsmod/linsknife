// Test RTTI runtime API
#include <stdio.h>
#include <string.h>
#include "include/tcc_rtti_runtime.h"

// Define structs with RTTI reflection
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
    struct Point p = {10, 20};
    struct Rectangle r = {{"left", "top"}, {"right", "bottom"}, "TestRect"};
    
    printf("=== RTTI Runtime Test ===\n\n");
    
    // Get RTTI for Point
    const tcc_rtti_type_t *point_type = tcc_rtti_get("MyPoint");
    if (point_type) {
        printf("Found RTTI for 'MyPoint':\n");
        tcc_rtti_print(point_type);
        
        // Access field using RTTI
        int *x_ptr = (int *)tcc_rtti_get_field_ptr(&p, point_type, "x");
        int *y_ptr = (int *)tcc_rtti_get_field_ptr(&p, point_type, "y");
        
        if (x_ptr && y_ptr) {
            printf("\n  Using RTTI to access Point fields:\n");
            printf("    p.x = %d (via RTTI: %d)\n", p.x, *x_ptr);
            printf("    p.y = %d (via RTTI: %d)\n", p.y, *y_ptr);
            
            // Modify via RTTI
            *x_ptr = 100;
            *y_ptr = 200;
            printf("    After modification: p.x=%d, p.y=%d\n", p.x, p.y);
        }
    } else {
        printf("RTTI for 'MyPoint' not found\n");
    }
    
    printf("\n");
    
    // Get RTTI for Rectangle
    const tcc_rtti_type_t *rect_type = tcc_rtti_get("MyRectangle");
    if (rect_type) {
        printf("Found RTTI for 'MyRectangle':\n");
        tcc_rtti_print(rect_type);
    } else {
        printf("RTTI for 'MyRectangle' not found\n");
    }
    
    printf("\n");
    
    // List all registered types
    int count = tcc_rtti_get_count();
    printf("Total registered types: %d\n", count);
    
    for (int i = 0; i < count; i++) {
        const tcc_rtti_type_t *t = tcc_rtti_get_at(i);
        if (t) {
            printf("  [%d] %s (size=%d, id=%d)\n", i, 
                   t->name ? t->name : "(anonymous)", t->size, t->type_id);
        }
    }
    
    printf("\n=== Test Complete ===\n");
    return 0;
}