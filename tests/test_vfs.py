"""
Tests for the Virtual File System (ZVfs)
"""

import os
import tempfile

from zvfs import (
    ZVfs,
    FSProvider,
    StringProvider,
    NullProvider,
    BindType,
    DEntryFile,
    DEntryDirectory,
    DEntryNotFound,
    ProviderNodeType,
)


class TestNullProvider:
    def test_stat_root_is_dir(self):
        provider = NullProvider()
        assert provider.stat(0) == ProviderNodeType.DIR

    def test_stat_nonroot_is_notfound(self):
        provider = NullProvider()
        assert provider.stat(1) == ProviderNodeType.NOTFOUND

    def test_walk_returns_nonzero(self):
        provider = NullProvider()
        nodeid = provider.walk("anything")
        assert nodeid == 1  # always returns 1 (NONE)

    def test_path_returns_null(self):
        provider = NullProvider()
        assert provider.path(0) == "[NULL]"


class TestVfsBasic:
    def test_initial_root(self):
        vfs = ZVfs()
        rootid = vfs.walk()
        entry = vfs.stat(rootid)
        # Initial root is a DEntryDirectory from NullProvider
        assert isinstance(entry, DEntryDirectory)

    def test_register_provider(self):
        """Register a provider and verify it gets an entry."""
        vfs = ZVfs()
        provider = NullProvider()
        entryid = vfs.register(provider)
        entry = vfs.stat(entryid)
        assert isinstance(entry, DEntryDirectory)


class TestFSProvider:
    def test_walk_to_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = os.path.join(tmpdir, "test.z")
            with open(filepath, "w") as f:
                f.write("hello")

            provider = FSProvider(rootpath=tmpdir, parentpath="")
            nodeid = provider.walk("test.z")
            assert provider.stat(nodeid) == ProviderNodeType.FILE

    def test_walk_to_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            subdir = os.path.join(tmpdir, "sub")
            os.makedirs(subdir)

            provider = FSProvider(rootpath=tmpdir, parentpath="")
            nodeid = provider.walk("sub")
            assert provider.stat(nodeid) == ProviderNodeType.DIR

    def test_walk_to_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            provider = FSProvider(rootpath=tmpdir, parentpath="")
            nodeid = provider.walk("nonexistent")
            assert provider.stat(nodeid) == ProviderNodeType.NOTFOUND

    def test_open_and_read(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = os.path.join(tmpdir, "test.z")
            with open(filepath, "w") as f:
                f.write("content")

            provider = FSProvider(rootpath=tmpdir, parentpath="")
            nodeid = provider.walk("test.z")
            fh = provider.open(nodeid)
            assert fh.read() == "content"
            fh.close()


class TestVfsWalk:
    def test_walk_to_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = os.path.join(tmpdir, "test.z")
            with open(filepath, "w") as f:
                f.write("hello")

            vfs = ZVfs()
            fsid = vfs.register(FSProvider(rootpath=tmpdir, parentpath=""))
            rootid = vfs.walk()
            rootid = vfs.bind(parentid=rootid, name=None, newid=fsid)

            entryid = vfs.walk(["test.z"])
            entry = vfs.stat(entryid)
            assert isinstance(entry, DEntryFile)

    def test_walk_to_subdir_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            subdir = os.path.join(tmpdir, "sub")
            os.makedirs(subdir)
            filepath = os.path.join(subdir, "test.z")
            with open(filepath, "w") as f:
                f.write("hello")

            vfs = ZVfs()
            fsid = vfs.register(FSProvider(rootpath=tmpdir, parentpath=""))
            rootid = vfs.walk()
            rootid = vfs.bind(parentid=rootid, name=None, newid=fsid)

            entryid = vfs.walk(["sub", "test.z"])
            entry = vfs.stat(entryid)
            assert isinstance(entry, DEntryFile)

    def test_walk_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            vfs = ZVfs()
            fsid = vfs.register(FSProvider(rootpath=tmpdir, parentpath=""))
            rootid = vfs.walk()
            rootid = vfs.bind(parentid=rootid, name=None, newid=fsid)

            entryid = vfs.walk(["nonexistent.z"])
            entry = vfs.stat(entryid)
            assert isinstance(entry, DEntryNotFound)


class TestVfsBind:
    def test_bind_instead(self):
        vfs = ZVfs()
        p1 = vfs.register(NullProvider())
        rootid = vfs.walk()
        new_root = vfs.bind(parentid=rootid, name=None, newid=p1)
        assert new_root != rootid

    def test_bind_before(self):
        """BEFORE means new mount is searched first."""
        with tempfile.TemporaryDirectory() as tmpdir1:
            with tempfile.TemporaryDirectory() as tmpdir2:
                # file only in tmpdir1
                with open(os.path.join(tmpdir1, "a.z"), "w") as f:
                    f.write("from1")
                # file only in tmpdir2
                with open(os.path.join(tmpdir2, "b.z"), "w") as f:
                    f.write("from2")

                vfs = ZVfs()
                fs1 = vfs.register(FSProvider(rootpath=tmpdir1, parentpath=""))
                fs2 = vfs.register(FSProvider(rootpath=tmpdir2, parentpath=""))

                rootid = vfs.walk()
                rootid = vfs.bind(parentid=rootid, name=None, newid=fs1)
                rootid = vfs.bind(
                    parentid=rootid,
                    name=None,
                    newid=fs2,
                    bindtype=BindType.BEFORE,
                )

                # b.z from fs2 (BEFORE) should be found
                eid = vfs.walk(["b.z"])
                entry = vfs.stat(eid)
                assert isinstance(entry, DEntryFile)

                # a.z from fs1 should still be found
                eid = vfs.walk(["a.z"])
                entry = vfs.stat(eid)
                assert isinstance(entry, DEntryFile)

    def test_bind_after(self):
        """AFTER means new mount is searched second."""
        with tempfile.TemporaryDirectory() as tmpdir1:
            with tempfile.TemporaryDirectory() as tmpdir2:
                with open(os.path.join(tmpdir1, "a.z"), "w") as f:
                    f.write("from1")
                with open(os.path.join(tmpdir2, "b.z"), "w") as f:
                    f.write("from2")

                vfs = ZVfs()
                fs1 = vfs.register(FSProvider(rootpath=tmpdir1, parentpath=""))
                fs2 = vfs.register(FSProvider(rootpath=tmpdir2, parentpath=""))

                rootid = vfs.walk()
                rootid = vfs.bind(parentid=rootid, name=None, newid=fs1)
                rootid = vfs.bind(
                    parentid=rootid,
                    name=None,
                    newid=fs2,
                    bindtype=BindType.AFTER,
                )

                # Both files should be found
                eid_a = vfs.walk(["a.z"])
                assert isinstance(vfs.stat(eid_a), DEntryFile)
                eid_b = vfs.walk(["b.z"])
                assert isinstance(vfs.stat(eid_b), DEntryFile)


class TestVfsOpen:
    def test_open_and_read(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = os.path.join(tmpdir, "test.z")
            with open(filepath, "w") as f:
                f.write("content here")

            vfs = ZVfs()
            fsid = vfs.register(FSProvider(rootpath=tmpdir, parentpath=""))
            rootid = vfs.walk()
            rootid = vfs.bind(parentid=rootid, name=None, newid=fsid)

            entryid = vfs.walk(["test.z"])
            openfile = vfs.open(entryid)
            data = openfile.filehandle.read()
            openfile.close()
            assert data == "content here"

    def test_getline(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = os.path.join(tmpdir, "test.z")
            with open(filepath, "w") as f:
                f.write("line1\nline2\nline3\n")

            vfs = ZVfs()
            fsid = vfs.register(FSProvider(rootpath=tmpdir, parentpath=""))
            rootid = vfs.walk()
            rootid = vfs.bind(parentid=rootid, name=None, newid=fsid)

            entryid = vfs.walk(["test.z"])
            line = vfs.getline(entryid, 2)
            assert line == "line2\n"


class TestStringProvider:
    def test_walk_to_file(self):
        provider = StringProvider(files={"test.z": "hello"})
        nodeid = provider.walk("test.z")
        assert provider.stat(nodeid) == ProviderNodeType.FILE

    def test_walk_to_missing(self):
        provider = StringProvider(files={"test.z": "hello"})
        nodeid = provider.walk("nope.z")
        assert provider.stat(nodeid) == ProviderNodeType.NOTFOUND

    def test_open_and_read(self):
        provider = StringProvider(files={"test.z": "content"})
        nodeid = provider.walk("test.z")
        fh = provider.open(nodeid)
        assert fh.read() == "content"
        fh.close()

    def test_subdirectory(self):
        provider = StringProvider(files={"sub/test.z": "nested"})
        subid = provider.walk("sub")
        assert provider.stat(subid) == ProviderNodeType.DIR
        fileid = provider.walk("test.z", subid)
        assert provider.stat(fileid) == ProviderNodeType.FILE
        fh = provider.open(fileid)
        assert fh.read() == "nested"
        fh.close()


class TestUnionCaching:
    def test_union_walk_same_result_twice(self):
        """Walking same name through union twice returns same DEntryID (proves caching)."""
        vfs = ZVfs()
        fs1 = vfs.register(StringProvider(files={"a.z": "hello"}))
        rootid = vfs.walk()
        rootid = vfs.bind(parentid=rootid, name=None, newid=fs1)

        eid1 = vfs.walk(["a.z"])
        eid2 = vfs.walk(["a.z"])
        assert eid1 == eid2

    def test_union_first_notfound_second_found(self):
        """Walk through union where first=NOTFOUND, second=found."""
        vfs = ZVfs()
        fs1 = vfs.register(StringProvider(files={"a.z": "from1"}))
        fs2 = vfs.register(StringProvider(files={"b.z": "from2"}))
        rootid = vfs.walk()
        rootid = vfs.bind(parentid=rootid, name=None, newid=fs1)
        rootid = vfs.bind(
            parentid=rootid, name=None, newid=fs2, bindtype=BindType.AFTER
        )

        eid = vfs.walk(["b.z"])
        entry = vfs.stat(eid)
        assert isinstance(entry, DEntryFile)

        # Second walk should use cache
        eid2 = vfs.walk(["b.z"])
        assert eid == eid2

    def test_union_subdir_walk(self):
        """Walk subdirectory path through union."""
        vfs = ZVfs()
        fs = vfs.register(StringProvider(files={"sub/test.z": "nested"}))
        rootid = vfs.walk()
        rootid = vfs.bind(parentid=rootid, name=None, newid=fs)

        eid = vfs.walk(["sub", "test.z"])
        entry = vfs.stat(eid)
        assert isinstance(entry, DEntryFile)


class TestGetlineEdgeCases:
    def test_getline_past_end(self):
        """getline() past end of file returns None."""
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = os.path.join(tmpdir, "test.z")
            with open(filepath, "w") as f:
                f.write("line1\nline2\n")

            vfs = ZVfs()
            fsid = vfs.register(FSProvider(rootpath=tmpdir, parentpath=""))
            rootid = vfs.walk()
            rootid = vfs.bind(parentid=rootid, name=None, newid=fsid)

            entryid = vfs.walk(["test.z"])
            line = vfs.getline(entryid, 999)
            assert line is None

    def test_getline_on_nonfile(self):
        """getline() on non-file entry returns None (not crash)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            subdir = os.path.join(tmpdir, "sub")
            os.makedirs(subdir)

            vfs = ZVfs()
            fsid = vfs.register(FSProvider(rootpath=tmpdir, parentpath=""))
            rootid = vfs.walk()
            rootid = vfs.bind(parentid=rootid, name=None, newid=fsid)

            entryid = vfs.walk(["sub"])
            entry = vfs.stat(entryid)
            assert isinstance(entry, DEntryDirectory)

            line = vfs.getline(entryid, 1)
            assert line is None

    def test_getline_on_notfound(self):
        """getline() on NOTFOUND entry returns None (not crash)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            vfs = ZVfs()
            fsid = vfs.register(FSProvider(rootpath=tmpdir, parentpath=""))
            rootid = vfs.walk()
            rootid = vfs.bind(parentid=rootid, name=None, newid=fsid)

            entryid = vfs.walk(["nonexistent.z"])
            entry = vfs.stat(entryid)
            assert isinstance(entry, DEntryNotFound)

            line = vfs.getline(entryid, 1)
            assert line is None
