/* a Transaction's destructor: one that was neither committed nor rolled back
   rolls back -- leaving its scope early, by return or by error, undoes it */
static void z_@Transaction@_destroy(z_@Transaction@_t* p) {
    if (!p) return;
    if (p->@MEMBER_Transaction.finished@) return;
    sqlite3* db = z_sqlite_tx_db(p);
    if (!sqlite3_get_autocommit(db)) sqlite3_exec(db, "ROLLBACK", NULL, NULL, NULL);
    p->@MEMBER_Transaction.finished@ = true;
}

