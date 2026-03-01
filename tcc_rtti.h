/* -------------------------------------------------------------------------
 * RTTI Support for TinyCC
 * Runtime Type Information structures for ELF emission
 * ------------------------------------------------------------------------- */

#ifndef TCC_RTTI_H
#define TCC_RTTI_H

#include <stdint.h>

/* Field flags */
#define RTTI_FIELD_IS_PTR      (1 << 0)
#define RTTI_FIELD_IS_ARRAY    (1 << 1)
#define RTTI_FIELD_IS_BITFIELD (1 << 2)

/* Runtime RTTI field info (written to ELF) */
typedef struct tcc_rtti_field_rt {
    int name_offset;      /* Field name offset in string table */
    int offset;           /* Field offset in struct */
    int type_id;          /* Type ID of the field */
    int flags;            /* Flags (is_ptr, is_array, is_bitfield) */
    int bit_offset;       /* Bit offset (for bitfields) */
    int bit_size;         /* Bit size (for bitfields) */
    int array_size;       /* Array size (if array, 0 otherwise) */
} tcc_rtti_field_rt_t;

/* Runtime RTTI struct info (written to ELF) */
typedef struct tcc_rtti_struct_rt {
    int name_offset;      /* Type name offset in string table */
    int size;             /* Struct size */
    int align;            /* Alignment requirement */
    int field_count;      /* Number of fields */
    uint32_t type_hash;   /* Type hash for fast comparison */
    int type_id;          /* Unique type ID */
    int reserved;         /* Reserved for alignment */
    /* Followed by: */
    /* tcc_rtti_field_rt_t fields[field_count]; */
    /* char strings[]; */
} tcc_rtti_struct_rt_t;

/* RTTI section name */
#define RTTI_SECTION_NAME ".rtti"

/* Magic number for RTTI section identification */
#define RTTI_MAGIC 0x52545449  /* "RTTI" in little-endian */

/* RTTI section header (at the beginning of .rtti section) */
typedef struct tcc_rtti_header {
    uint32_t magic;       /* RTTI_MAGIC */
    uint32_t version;     /* Version number */
    uint32_t count;       /* Number of RTTI entries */
    uint32_t reserved;    /* Reserved */
} tcc_rtti_header_t;

#define RTTI_VERSION 1

#endif /* TCC_RTTI_H */