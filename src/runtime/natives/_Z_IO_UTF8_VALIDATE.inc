/* 1 when [data, data+len) is valid UTF-8, else 0: the count itself is the
   shared validator's (_Z_UTF8_VALID_UPTO, which readLine needs) */
static uint64_t z_utf8_valid_upto(const uint8_t* data, uint64_t len);
static int z_io_utf8_is_valid(const uint8_t* data, uint64_t len);
static int z_io_utf8_is_valid(const uint8_t* data, uint64_t len) {
    return z_utf8_valid_upto(data, len) == len;
}

/* Box a null-payload ioerror arm into result(string, ioerror). */
static z_@Result_String_IoError@_t z_io_String_err_arm(z_@IoError@_tag_t tag);
static z_@Result_String_IoError@_t z_io_String_err_arm(z_@IoError@_tag_t tag) {
    z_@Result_String_IoError@_t result = {0};
    z_@IoError@_t* boxed = (z_@IoError@_t*)z_xmalloc(sizeof(z_@IoError@_t));
    z_@IoError@_t e = {0};
    e.tag = tag;
    *boxed = e;
    result.tag = @ARM_Result_String_IoError.err@;
    result.data = boxed;
    return result;
}

/* Box a string value into result(string, ioerror) ok arm. Takes
   ownership of `s` (its heap buffer moves into the boxed copy). */
static z_@Result_String_IoError@_t z_io_String_ok(z_@String@_t s);
static z_@Result_String_IoError@_t z_io_String_ok(z_@String@_t s) {
    z_@Result_String_IoError@_t result = {0};
    z_@String@_t* boxed = (z_@String@_t*)z_xmalloc(sizeof(z_@String@_t));
    *boxed = s;
    result.tag = @ARM_Result_String_IoError.ok@;
    result.data = boxed;
    return result;
}


