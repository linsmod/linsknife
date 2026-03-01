/*
 * RTTI (Run-Time Type Information) Support for TCC
 *
 * This file implements the emission of RTTI data to ELF format.
 */

#include "tcc.h"
#include <stdio.h>
#include "tcc_rtti.h"

/* External reference to global RTTI table */
extern RTTIInfo *rtti_table;

/* Helper function to write a string to a section */
static void section_write_string(Section *s, const char *str)
{
    if (str) {
        int len = strlen(str) + 1;
        section_ptr_add(s, len);
        memcpy(s->data + s->data_offset - len, str, len);
    } else {
        /* Write null terminator for NULL strings */
        section_ptr_add(s, 1);
        s->data[s->data_offset - 1] = 0;
    }
}

/* Helper function to write a 32-bit integer */
static void section_write_int32(Section *s, int32_t val)
{
    section_ptr_add(s, 4);
    write32le(s->data + s->data_offset - 4, val);
}

/* Helper function to write a 64-bit integer */
static void section_write_int64(Section *s, int64_t val)
{
    section_ptr_add(s, 8);
    write64le(s->data + s->data_offset - 8, val);
}

/* Helper function to write a pointer-sized value */
static void section_write_ptr(Section *s, addr_t val)
{
    section_ptr_add(s, PTR_SIZE);
#if PTR_SIZE == 8
    write64le(s->data + s->data_offset - PTR_SIZE, val);
#else
    write32le(s->data + s->data_offset - PTR_SIZE, val);
#endif
}

/* Forward declaration */
static addr_t emit_rtti_info(TCCState *s1, RTTIInfo *rtti);

/* Emit a single RTTI field structure */
static void emit_rtti_field(TCCState *s1, Section *s, struct RTTIField *field)
{
    /* Field structure:
     * - name_offset (pointer/string)
     * - type_id (int32)
     * - type_info_ptr (pointer, may be NULL)
     * - offset (int32)
     * - is_bitfield (int8)
     * - bit_offset (int8)
     * - bit_size (int8)
     * - padding (int8)
     */

    /* Write field name offset (will need relocation) */
    if (field->name) {
        /* For now, write the string inline */
        addr_t name_offset = s->data_offset;
        section_write_string(s, field->name);
        /* Go back and write the offset */
        addr_t current = s->data_offset;
        s->data_offset = name_offset - PTR_SIZE;
        section_write_ptr(s, name_offset);
        s->data_offset = current;
    } else {
        section_write_ptr(s, 0);
    }

    /* Write type_id */
    section_write_int32(s, field->type_id);

    /* Write type_info pointer (for nested struct types) */
    if (field->type_info) {
        /* Recursively emit nested type info */
        addr_t type_info_addr = emit_rtti_info(s1, field->type_info);
        section_write_ptr(s, type_info_addr);
    } else {
        section_write_ptr(s, 0);
    }

    /* Write offset */
    section_write_int32(s, field->offset);

    /* Write bitfield info */
    section_ptr_add(s, 4);
    unsigned char *bf_data = s->data + s->data_offset - 4;
    bf_data[0] = field->is_bitfield ? 1 : 0;
    bf_data[1] = (unsigned char)field->bit_offset;
    bf_data[2] = (unsigned char)field->bit_size;
    bf_data[3] = 0; /* padding */
}

/* Emit RTTI info for a single type */
static addr_t emit_rtti_info(TCCState *s1, RTTIInfo *rtti)
{
    Section *s;
    addr_t start_offset;

    if (!rtti)
        return 0;

    /* Create or get the RTTI section */
    s = find_section(s1, ".tcc_rtti");
    if (!s) {
        s = new_section(s1, ".tcc_rtti", SHT_PROGBITS, SHF_ALLOC);
    }

    start_offset = s->data_offset;

    /* RTTI Header structure:
     * - type_id (int32)
     * - size (int32)
     * - align (int32)
     * - type_hash (int32)
     * - name_offset (pointer)
     * - field_count (int32)
     * - fields_array (pointer)
     * - next (pointer, for linked list)
     */

    /* Write type_id */
    section_write_int32(s, rtti->type_id);

    /* Write size */
    section_write_int32(s, rtti->size);

    /* Write alignment */
    section_write_int32(s, rtti->align);

    /* Write type hash */
    section_write_int32(s, rtti->type_hash);

    /* Write name offset */
    if (rtti->name) {
        addr_t name_offset = s->data_offset;
        section_write_string(s, rtti->name);
        /* Write offset to name */
        addr_t current = s->data_offset;
        s->data_offset = name_offset - PTR_SIZE;
        section_write_ptr(s, name_offset);
        s->data_offset = current;
    } else {
        section_write_ptr(s, 0);
    }

    /* Write field count */
    section_write_int32(s, rtti->field_count);

    /* Write fields array pointer */
    if (rtti->fields && rtti->field_count > 0) {
        addr_t fields_offset = s->data_offset;

        /* Emit each field */
        for (int i = 0; i < rtti->field_count; i++) {
            emit_rtti_field(s1, s, &rtti->fields[i]);
        }

        /* Write offset to fields array */
        addr_t current = s->data_offset;
        s->data_offset = fields_offset - PTR_SIZE;
        section_write_ptr(s, fields_offset);
        s->data_offset = current;
    } else {
        section_write_ptr(s, 0);
    }

    /* Write next pointer (0 for now, will be filled in later if needed) */
    section_write_ptr(s, 0);

    return start_offset;
}

/* Main function to emit all RTTI data */
ST_FUNC void tcc_emit_rtti(TCCState *s1)
{
    RTTIInfo *rtti;
    int count = 0;
    Section *s;
    Sym *sym;
    CType type;
    int i;
    addr_t *type_offsets;

    /* Count RTTI entries */
    for (rtti = rtti_table; rtti; rtti = rtti->next) {
        count++;
    }

    if (count == 0) {
        /* No RTTI data to emit */
        return;
    }

    /* Allocate array to store type offsets */
    type_offsets = tcc_malloc(count * sizeof(addr_t));

    printf("emit_rtti_info count=%d\n",count);
    /* Create RTTI section */
    s = new_section(s1, ".tcc_rtti", SHT_PROGBITS, SHF_ALLOC);

    /* Write RTTI header:
     * - magic number: "TCC1" for version 1
     * - count: number of RTTI entries
     * - reserved: for future use
     */
    section_ptr_add(s, 4);
    memcpy(s->data + s->data_offset - 4, "TCC1", 4);
    section_write_int32(s, count);
    section_write_int32(s, 0); /* reserved */
    section_write_int32(s, 0); /* reserved */

    /* Emit each RTTI entry and store offsets */
    i = 0;
    for (rtti = rtti_table; rtti; rtti = rtti->next, i++) {
        type_offsets[i] = emit_rtti_info(s1, rtti);
    }

    /* Now create the global RTTI table array for runtime access */
    /* Create a symbol for __tcc_rtti_count */
    int count_tok = tok_alloc_const("__tcc_rtti_count");
    type.t = VT_INT;
    type.ref = NULL;
    sym = global_identifier_push(count_tok, type.t | VT_EXTERN, 0);
    
    /* Create the data section for the count */
    Section *data_sec = data_section;
    addr_t count_offset = section_add(data_sec, sizeof(int), 4);
    write32le(data_sec->data + count_offset, count);
    put_extern_sym(sym, data_sec, count_offset, sizeof(int));

    /* Create a symbol for __tcc_rtti_table */
    int table_tok = tok_alloc_const("__tcc_rtti_table");
    type.t = VT_PTR;
    mk_pointer(&type); /* pointer to pointer */
    sym = global_identifier_push(table_tok, type.t | VT_EXTERN, 0);
    
    /* Create the table: array of pointers to RTTI entries */
    addr_t table_offset = section_add(data_sec, count * PTR_SIZE, PTR_SIZE);
    for (i = 0; i < count; i++) {
        /* Each entry is a pointer to the RTTI data in .tcc_rtti section */
#if PTR_SIZE == 8
        write64le(data_sec->data + table_offset + i * PTR_SIZE, 
                  s->sh_addr + type_offsets[i]);
#else
        write32le(data_sec->data + table_offset + i * PTR_SIZE, 
                  s->sh_addr + type_offsets[i]);
#endif
    }
    put_extern_sym(sym, data_sec, table_offset, count * PTR_SIZE);

    tcc_free(type_offsets);
}

/* Function to get RTTI info for a type by name (for runtime use) */
ST_FUNC RTTIInfo *tcc_get_rtti_by_name(const char *name)
{
    RTTIInfo *rtti;
    
    for (rtti = rtti_table; rtti; rtti = rtti->next) {
        if (rtti->name && strcmp(rtti->name, name) == 0) {
            return rtti;
        }
    }
    
    return NULL;
}

/* Function to get RTTI info for a type by type_id */
ST_FUNC RTTIInfo *tcc_get_rtti_by_id(int type_id)
{
    RTTIInfo *rtti;
    
    for (rtti = rtti_table; rtti; rtti = rtti->next) {
        if (rtti->type_id == type_id) {
            return rtti;
        }
    }
    
    return NULL;
}

/* Function to get RTTI info for a type by hash */
ST_FUNC RTTIInfo *tcc_get_rtti_by_hash(uint32_t hash)
{
    RTTIInfo *rtti;
    
    for (rtti = rtti_table; rtti; rtti = rtti->next) {
        if (rtti->type_hash == hash) {
            return rtti;
        }
    }
    
    return NULL;
}

/* Debug function to print RTTI info */
#ifdef TCC_RTTI_DEBUG
ST_FUNC void tcc_print_rtti_info(RTTIInfo *rtti)
{
    int i;
    
    if (!rtti) {
        printf("RTTI: NULL\n");
        return;
    }
    
    printf("RTTI Info:\n");
    printf("  Name: %s\n", rtti->name ? rtti->name : "(anonymous)");
    printf("  Type ID: %d\n", rtti->type_id);
    printf("  Size: %d\n", rtti->size);
    printf("  Align: %d\n", rtti->align);
    printf("  Hash: 0x%08x\n", rtti->type_hash);
    printf("  Field Count: %d\n", rtti->field_count);
    
    if (rtti->fields) {
        for (i = 0; i < rtti->field_count; i++) {
            struct RTTIField *f = &rtti->fields[i];
            printf("    Field %d: %s\n", i, f->name ? f->name : "(anonymous)");
            printf("      Type ID: %d\n", f->type_id);
            printf("      Offset: %d\n", f->offset);
            if (f->is_bitfield) {
                printf("      Bitfield: offset=%d, size=%d\n", 
                       f->bit_offset, f->bit_size);
            }
        }
    }
}
#endif /* TCC_RTTI_DEBUG */