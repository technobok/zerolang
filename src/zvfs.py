"""
ZVfs - simple readonly virtual filesystem for the compiler

Allows creation of a namespace (via bind()) to present a unified hierarchy of
dir-like and file-like objects to the compiler.
"""

import os
import threading
from abc import ABC, abstractmethod
from enum import IntEnum
from typing import IO, Optional, List, Dict, Tuple, NewType
from dataclasses import dataclass, field


DEntryID = NewType("DEntryID", int)
ProviderID = NewType("ProviderID", int)
NodeID = NewType("NodeID", int)


class DEntryType(IntEnum):
    """
    DEntryType - simple stat for an entry in the VFS
    """

    NOTFOUND = 0  # entry doesn't exist
    FILE = 1
    DIR = 2
    UNION = 3


class BindType(IntEnum):
    """
    BindType - type of mount for ZVfs.bind() call
    """

    INSTEAD = 0  # new mount will replace existing entries
    BEFORE = 1  # new mount will be searched before existing entries
    AFTER = 2  # new mount will be searched after existing entries


class ProviderNodeType(IntEnum):
    """
    ProviderNodeType - node type from a provider
    """

    NOTFOUND = 0  # entry does not exist
    FILE = 1
    DIR = 2


@dataclass
class DEntry:
    """
    DEntry = the parent of the Entry hierarchy
    Do not instantiate directly
    """

    entrytype: DEntryType
    # the parent of this id (can follow to find path)
    # the parent of a root is itself (parentid == entryid)
    parentid: Optional[DEntryID]  # do we need parentid?
    cache: Dict[str, DEntryID] = field(init=False, default_factory=dict)


@dataclass
class DEntryNotFound(DEntry):
    """
    DEntryNotFound = there is no entry. Stored in the cache to prevent retrying lookups

    links to the provider are maintained so that paths can be calculated for error messages
    """

    entrytype: DEntryType = field(default=DEntryType.NOTFOUND, init=False)
    providerid: ProviderID  # provider that holds this missing file
    nodeid: NodeID  # the 'inode' of this missing entry (unique to ProviderID)


@dataclass
class DEntryFile(DEntry):
    """
    DEntryFile - a file-like entry
    """

    entrytype: DEntryType = field(default=DEntryType.FILE, init=False)
    providerid: ProviderID  # provider that holds this file
    nodeid: NodeID  # the 'inode' of this entry (unique to ProviderID)


@dataclass
class DEntryDirectory(DEntry):
    """
    DEntryDirectory - a directory-like entry
    """

    entrytype: DEntryType = field(default=DEntryType.DIR, init=False)
    providerid: ProviderID  # provider that holds the contents of this entry
    nodeid: NodeID  # the inode of this entry (unique to ProviderID)


@dataclass
class DEntryUnion(DEntry):
    """
    DEntryUnion - a union (bind/mountpoint type) entry
    """

    entrytype: DEntryType = field(default=DEntryType.UNION, init=False)
    first: DEntryID  # first priority, try here first
    second: Optional[DEntryID]  # second priority, try here if not in first


class DEntryTable:
    """
    DEntryTable - table for building and holding DEntries and assigning an
        autoincrementing id.
    """

    def __init__(self) -> None:
        self._table: List[DEntry] = []
        self._lock = threading.Lock()

    def __getitem__(self, index: DEntryID) -> DEntry:
        return self._table[index]

    def _append(self, entry: DEntry) -> DEntryID:
        """
        _append - append a new DEntry into the table returning its id

        This method is locked to prevent a race if threaded
        """
        with self._lock:
            idx = DEntryID(len(self._table))
            self._table.append(entry)  # will be appended at idx
        return idx

    def none(
        self, parentid: DEntryID, providerid: ProviderID, nodeid: NodeID
    ) -> DEntryID:
        """
        none - create a DEntryNotFound entry
        """
        entry = DEntryNotFound(parentid=parentid, providerid=providerid, nodeid=nodeid)
        return self._append(entry)

    def file(
        self, parentid: DEntryID, providerid: ProviderID, nodeid: NodeID
    ) -> DEntryID:
        """
        DEntryFile - create a file entry

        providerid: ProviderID of the underlying VFS provider
        nodeid: NodeID from the underlying VFS provider for this file
        """
        entry = DEntryFile(parentid=parentid, providerid=providerid, nodeid=nodeid)
        return self._append(entry)

    def directory(
        self, parentid: Optional[DEntryID], providerid: ProviderID, nodeid: NodeID
    ) -> DEntryID:
        """
        DEntryDirectory - create a directory entry

        providerid: ProviderID of the underlying VFS provider
        nodeid: NodeID from the underlying VFS provider for this directory
        """
        entry = DEntryDirectory(parentid=parentid, providerid=providerid, nodeid=nodeid)
        return self._append(entry)

    def union(
        self, parentid: DEntryID, first: DEntryID, second: Optional[DEntryID]
    ) -> DEntryID:
        """
        DEntryUnion - create a union entry

        first: the first DEntryID (directory) to search from this point
        next: the next DEntryID (directory) to search from this point if subentry
            is not found in 'first'. If supplied, then this is a Union mountpoint.
            May be none, indicating that only 'first' should be searched (as in a
            traditional mount point).
        """
        entry = DEntryUnion(parentid=parentid, first=first, second=second)
        return self._append(entry)


class ZVfsProvider(ABC):
    """
    Abstract FS Provider interface. ZVfsProvider instances must impliment this
    """

    @abstractmethod
    def walk(self, name: str, parent: NodeID = NodeID(0)) -> NodeID:
        """
        walk - walk from the parent via path, return the Node at the destination
        if available.

        name: name of child component to walk to
        parent: parent node to start from. Default to 0 - the root node

        Return the NodeID at the destination.
        May throw IO errors (file doesn't exist, permission errors)
        """

    @abstractmethod
    def open(self, item: NodeID) -> IO[str]:
        """
        open - open the file item for reading
        item: id of fileitem to open

        Returns a filelike object for reading
        May throw IO errors (file doesn't exist, permission errors)
        """

    @abstractmethod
    def stat(self, item: NodeID) -> ProviderNodeType:
        """
        stat - determine if item is a directory
        item: id of item

        Returns ProviderNodeType
        """

    @abstractmethod
    def path(self, item: NodeID) -> str:
        """
        path - return the underlying path for this named entry within this
        driver
        name: name of entry (no '/'s)

        Returns a path string
        May throw IO errors (file doesn't exist, permission errors)
        """


@dataclass
class ZVfsOpenFile:
    """
    An open file handle.
    """

    entryid: DEntryID
    filehandle: IO[str]

    def close(self) -> None:
        """
        close the underlying file
        """
        self.filehandle.close()


class ZVfs:
    """
    Virtual File System

    Does entryid generation and maintenance and cache over a tree of
    ZVfsDrivers
    """

    def __init__(self) -> None:
        self.entrytable = DEntryTable()
        self._providertable: List[ZVfsProvider] = []
        self._lock = threading.Lock()

        self.rootid: DEntryID = self.register(NullProvider())

    def stat(self, entryid: DEntryID) -> DEntry:
        """
        stat - return the DEntry for a DEntryID
        """
        return self.entrytable[entryid]

    def register(self, provider: ZVfsProvider) -> DEntryID:
        """
        register - register a new provider within this VFS

        provider = the ZVfsProvider to register

        Returns a new DEntryID. The provider is 'anonymously' registered: it can
        be used via the entryid as returned but it will need to be passed to a
        bind() call to include it within the VFS namespace.

        Assumes the provider is a directory tree (not just a file)
        Mounts the root (NodeID = 0) of the provider
        """
        # store the provider
        with self._lock:
            providerid = ProviderID(len(self._providertable))
            self._providertable.append(provider)

        # create the DEntry. NodeID(0) is the root
        entryid = self.entrytable.directory(
            parentid=None, providerid=providerid, nodeid=NodeID(0)
        )

        return entryid

    def walk(
        self, path: Optional[List[str]] = None, parentid: Optional[DEntryID] = None
    ) -> DEntryID:
        """
        walk - navigate to a child entry using a relative path from a parent entry
        unitpath: list of path component names
        parentid: DEntryID to walk from, defaults to root entryid

        Returns a DEntryID for the child. May be DEntryNotFound if not found.

        walk() without arguments will return the current root.
        """
        # pylint: disable=R0912
        # print(f"walk(): path:{path!r} parentid:{parentid!r}")
        if path is None:
            path = []
        if parentid:
            entry: DEntry = self.entrytable[parentid]
            newentryid: DEntryID = parentid
        else:
            # start from root
            entry = self.entrytable[self.rootid]
            newentryid = self.rootid

        for p in path:
            if isinstance(entry, (DEntryDirectory, DEntryNotFound, DEntryFile)):
                # always try to walk (even from File or None) so we get path for error
                cachedentryid = entry.cache.get(p, None)
                if cachedentryid:
                    newentryid = cachedentryid
                    entry = self.entrytable[newentryid]
                    continue  # got it from cache

                # try to look it up in Provider
                provider = self._providertable[entry.providerid]
                newnodeid = provider.walk(p, entry.nodeid)
                newnodeidstat = provider.stat(newnodeid)
                if newnodeidstat == ProviderNodeType.FILE:
                    newentryid = self.entrytable.file(
                        parentid=newentryid,
                        providerid=entry.providerid,
                        nodeid=newnodeid,
                    )
                elif newnodeidstat == ProviderNodeType.DIR:
                    newentryid = self.entrytable.directory(
                        parentid=newentryid,
                        providerid=entry.providerid,
                        nodeid=newnodeid,
                    )
                else:  # ProviderNodeType.NONE
                    # cannot find it, cache a none entry
                    # newentryid = self.entrytable.none(parentid=newentryid)
                    newentryid = self.entrytable.none(
                        parentid=newentryid,
                        providerid=entry.providerid,
                        nodeid=newnodeid,
                    )

                entry.cache[p] = newentryid  # cache it in parent for next time
                entry = self.entrytable[newentryid]
                continue

            # old way....
            # if isinstance(entry, (DEntryNotFound, DEntryFile)):
            #     # can't walk from None or File
            #     # cache a none entry
            #     newentryid = self.entrytable.none(parentid=newentryid)
            #     entry.cache[p] = newentryid
            #     entry = self.entrytable[newentryid]
            #     continue

            # if isinstance(entry, DEntryDirectory):
            #     cachedentryid = entry.cache.get(p, None)
            #     if cachedentryid:
            #         newentryid = cachedentryid
            #         entry = self.entrytable[newentryid]
            #         continue  # got it from cache

            #     # try to look it up in Provider
            #     provider = self._providertable[entry.providerid]
            #     newnodeid = provider.walk(p, entry.nodeid)
            #     newnodeidstat = provider.stat(newnodeid)
            #     if newnodeidstat == ProviderNodeType.FILE:
            #         newentryid = self.entrytable.file(
            #             parentid=newentryid,
            #             providerid=entry.providerid,
            #             nodeid=newnodeid,
            #         )
            #     elif newnodeidstat == ProviderNodeType.DIR:
            #         newentryid = self.entrytable.directory(
            #             parentid=newentryid,
            #             providerid=entry.providerid,
            #             nodeid=newnodeid,
            #         )
            #     else:  # ProviderNodeType.NONE
            #         # cannot find it, cache a none entry
            #         newentryid = self.entrytable.none(parentid=newentryid)

            #     entry.cache[p] = newentryid  # cache it in parent for next time
            #     entry = self.entrytable[newentryid]
            #     continue

            if isinstance(entry, DEntryUnion):
                # walk in first
                newentryid = self.walk(path=[p], parentid=entry.first)
                newentry = self.entrytable[newentryid]

                if not isinstance(newentry, DEntryNotFound) or (entry.second is None):
                    # got something or no second, return what we got
                    entry = newentry
                    continue

                # walk in second, and return whatever is returned
                newentryid = self.walk(path=[p], parentid=entry.second)
                entry = self.entrytable[newentryid]
                continue

        return newentryid

    def bind(
        self,
        parentid: DEntryID,
        name: Optional[str],
        newid: DEntryID,
        bindtype: BindType = BindType.INSTEAD,
    ) -> DEntryID:
        """
        bind - bind an entry over a another in the VFS namespace. Bind newid at
        parentid/name. To replace the current root, pass parentid=currentroot
        (from walk()) and name=None

        parentid = dir to mount into. Obtained from walk()
        name = name of the bind point in the parentid (does not need to exist
            for INSTEAD but must exist for BEFORE/AFTER or IOError will be raised)
            Can be None (ONLY) when replacing an existing root.
        newid = id to mount. Obtained from walk() or register(). Should usually be dir
        bindtype = type of mount (over - INSTEAD; or union - BEFORE/AFTER). Defaults
            to INSTEAD. Must be INSTEAD if newid is a file, otherwise IOError.

        Returns the new entryid of the bindpoint. Can raise IOError
        """
        parent = self.entrytable[parentid]
        if name is None:
            # replacing root
            if parentid != self.rootid:
                raise IOError("name must be supplied to bind() (unless replacing root)")
            if bindtype == BindType.INSTEAD:
                newentryid = self.entrytable.union(
                    parentid=parentid, first=newid, second=None
                )
            elif bindtype == BindType.BEFORE:
                newentryid = self.entrytable.union(
                    parentid=parentid, first=newid, second=parentid
                )
            elif bindtype == BindType.AFTER:
                newentryid = self.entrytable.union(
                    parentid=parentid, first=parentid, second=newid
                )
            else:
                # bad bindtype. Cannot happen, all handled above
                raise IOError("Error during Bind()")

            self.rootid = newentryid
            return newentryid

        if bindtype == BindType.INSTEAD:
            newentryid = self.entrytable.union(
                parentid=parentid, first=newid, second=None
            )
        else:
            oldid = parent.cache.get(name, None)
            if not oldid:
                raise IOError("Valid name must be supplied to bind() for BEFORE/AFTER")
            oldentryid = oldid

            # no stat is done for oldentryid or newid. They may be NONE or
            # FILE which would not find any child entries, but would still
            # work...

            if bindtype == BindType.BEFORE:
                newentryid = self.entrytable.union(
                    parentid=parentid, first=newid, second=oldentryid
                )
            elif bindtype == BindType.AFTER:
                newentryid = self.entrytable.union(
                    parentid=parentid, first=oldentryid, second=newid
                )
            else:
                # bad bindtype. Cannot happen, all handled above
                raise IOError("Error during Bind()")

        parent.cache[name] = newentryid  # create/replace cache entry
        return newentryid

    def path(self, entryid: DEntryID) -> Optional[str]:
        """
        path - return the full path given an DEntryID, as stored in the VFS.

        This is not particularly efficient since we do not map DEntryID's to
        names and need to iterate over all entries at each level

        returns the path or None if there was an error

        """
        eid: DEntryID = entryid
        entry = self.entrytable[eid]
        # print(f"Entry:{eid}:{entry!r}")
        pathlist: List[str] = []
        while True:
            # parentid: Optional[DEntryID] = entry.parentid
            if entry.parentid is None:
                break  # at root
            parentid: DEntryID = entry.parentid
            parententry = self.entrytable[parentid]
            found = False
            # print(f"cache:{parententry.cache!r}")
            for name, cid in parententry.cache.items():
                # print(name, cid, "Looking for", entryid)
                if cid == eid:
                    pathlist.append(name)
                    found = True
                    # print("got", entryid)
                    break
            if not found:
                pathlist.append("[NOT FOUND]")

            eid = parentid
            entry = parententry

        result = "/".join(reversed(pathlist))
        # print(repr(result))
        return result

    def pathfromdriver(self, entryid: DEntryID) -> Optional[str]:
        """
        path - return the full path given an DEntryID, as described by the driver

        returns the path or None if there was an error

        """
        entry = self.entrytable[entryid]
        if isinstance(entry, (DEntryFile, DEntryDirectory, DEntryNotFound)):
            # even if DEntryNotFound, we still have the path
            provider = self._providertable[entry.providerid]
            return provider.path(entry.nodeid)

        if isinstance(entry, DEntryUnion):
            return "[UNION]"  # no underlying path for a union

        raise IOError("This cannot happen")

    def open(self, entryid: DEntryID) -> ZVfsOpenFile:
        """
        open - open a file id for reading

        Returns a file like object
        Can throw IOError if there are issues opening file
        """
        entry = self.entrytable[entryid]

        if not isinstance(entry, DEntryFile):
            raise IOError("Not a file")

        provider = self._providertable[entry.providerid]
        filehandle = provider.open(entry.nodeid)
        return ZVfsOpenFile(entryid=entryid, filehandle=filehandle)

    def getline(self, entryid: DEntryID, lineno: int) -> Optional[str]:
        """
        getline - return a line from a file id. Convenience method.

        entryid: a entryid previously returned from walk
        lineno: line number (1 based) to retrieve.

        Return None if the line cannot be found. May throw IO errors.
        """
        f = self.open(entryid)
        filehandle = f.filehandle
        result: Optional[str] = None
        currline = 0
        while True:
            currline += 1
            line = filehandle.readline()
            if not line:
                break
            if currline == lineno:
                result = line
                break
        f.close()
        return result


class NullProvider(ZVfsProvider):
    """
    NullProvider - a dummy provider. Used as the initial root.
    """

    def __init__(self) -> None:
        pass

    def walk(self, name: str, parent: NodeID = NodeID(0)) -> NodeID:
        """
        walk - walk from the parent via path, return the Node at the destination
        if available.

        name: name of child component to walk to
        parent: parent node to start from. Default to 0 - the root node

        Return the NodeID at the destination if possible, None otherwise.
        May throw IO errors (file doesn't exist, permission errors)
        """
        del name, parent
        return NodeID(1)  # cannot walk anywhere, 1 == NONE

    def open(self, item: NodeID) -> IO[str]:
        """
        open - open the file item for reading
        item: id of fileitem to open

        Returns a filelike object for reading
        May throw IO errors (file doesn't exist, permission errors)
        """
        del item
        raise IOError("NullProvider cannot open for read")

    def stat(self, item: NodeID) -> ProviderNodeType:
        """
        isdir - determine if item is a directory
        item: id of item

        Returns ProviderNodeType.DIR if a directory, NOTFOUND otherwise
        """
        # Parent is DIR, all others do not exist
        if item == 0:
            return ProviderNodeType.DIR
        return ProviderNodeType.NOTFOUND

    def path(self, item: NodeID) -> str:
        """
        path - return the underlying path for this named entry within this
        driver
        name: name of entry (no '/'s)

        Returns a path string
        May throw IO errors (file doesn't exist, permission errors)
        """
        del item
        return "[NULL]"


class FSProvider(ZVfsProvider):
    """
    FSProvider - a provider that wraps a standard filesystem.
    """

    def __init__(self, rootpath: str, parentpath: str):
        self._rootpath = rootpath
        # pathtable is a list of tuples of (path, isdir)
        self._pathtable: List[Tuple[str, ProviderNodeType]] = []
        self._lock = threading.Lock()

        path = os.path.join(rootpath, parentpath)
        if not os.path.isdir(path):
            raise IOError(f"Root [{path}] is not a directory")

        # store the parent
        # self._appendpath(rootpath, nodetype=ProviderNodeType.DIR)
        self._appendpath(parentpath, nodetype=ProviderNodeType.DIR)

    def _appendpath(self, path: str, nodetype: ProviderNodeType) -> NodeID:
        """
        _appendpath - append a path to the table return the index into the table
        """
        with self._lock:
            idx = NodeID(len(self._pathtable))
            self._pathtable.append((path, nodetype))  # appended at idx
        return idx

    def walk(self, name: str, parent: NodeID = NodeID(0)) -> NodeID:
        """
        walk - walk from the parent via path, return the Node at the destination

        name: name of child component to walk to
        parent: parent node to start from. Default to 0 - the root node

        Return the NodeID at the destination
        May throw IO errors (file doesn't exist, permission errors)
        """
        parentpath, _ = self._pathtable[parent]
        # itempath = os.path.join(parentpath, name)
        itempath = os.path.join(parentpath, name)
        itempathfull = os.path.join(self._rootpath, itempath)
        # print(f"FSProvider.walk(): {itempath}")
        if not os.path.exists(itempathfull):
            nodetype = ProviderNodeType.NOTFOUND
        elif os.path.isdir(itempathfull):
            nodetype = ProviderNodeType.DIR
        elif os.path.isfile(itempathfull):
            nodetype = ProviderNodeType.FILE
        else:
            nodetype = ProviderNodeType.NOTFOUND

        return self._appendpath(itempath, nodetype=nodetype)

    def open(self, item: NodeID) -> IO[str]:
        """
        open - open the file item for reading
        item: id of fileitem to open

        Returns a filelike object for reading
        May throw IO errors (file doesn't exist, permission errors)
        """

        # print(f"FSProvider.open(): {item}")
        path, nodetype = self._pathtable[item]
        if nodetype != ProviderNodeType.FILE:
            raise IOError("This is not a file")
        pathfull = os.path.join(self._rootpath, path)
        # newline="" to pass through newline characters without conversion
        return open(pathfull, mode="r", encoding="utf8", newline="")

    def stat(self, item: NodeID) -> ProviderNodeType:
        """
        stat - determine if item is a directory/file/missing
        item: id of item

        Returns ProviderNodeType
        """
        _, nodetype = self._pathtable[item]
        return nodetype

    def path(self, item: NodeID) -> str:
        """
        path - return the underlying path for this named entry within this
        driver
        name: name of entry (no '/'s)

        Returns a path string
        May throw IO errors (file doesn't exist, permission errors)
        """
        path, _ = self._pathtable[item]
        return path
