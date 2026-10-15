/* cobjdemo.answer -- what the unit's own C source, linked from the object
   cache, answers. */
int cobj_answer(void);
static int64_t z_cobjdemo_answer(void) {
    return (int64_t)cobj_answer();
}

