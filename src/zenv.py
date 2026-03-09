#!/usr/bin/python3
"""
ZeroLang Environment for parser
"""


class Environment:
    """
    Environment or Symboltable
    """


# TODO: rename to SymbolTable and SymbolFrame ????????????????????
# @dataclass
# class ParserFrame:
#     """
#     ParserFrame - a single frame in the Environment for the Parser containing
#     Expressions.
#     defs are Expressions. Used in Units and Code. Forward references are
#     allowed for Units only.
#     """

#     frametype: NodeType

#     # definitions local to this frame. Key is name of var, value is unused (use None)
#     # relies on dict being ordered
#     defs: Dict[str, None] = field(init=False, default_factory=dict)

#     # references so far unresolved at this frame. Key is name of var, value is Token
#     # where first used (for error reporting only)
#     # relies on dict being ordered
#     # at end of frame, all of these need to be resolved or promoted
#     refs: Dict[str, Token] = field(init=False, default_factory=dict)


# # class ParserEnvironment:
# #     pass


# class ParserEnvironment:
#     """
#     ParserEnvironment - lexical environment for the Parser for tracking
#     definitions and forward references.
#     """

#     def __init__(self) -> None:
#         """
#         create a new blank Environment
#         """
#         self.frames: List[ParserFrame] = []

#     def __enter__(self, nodetype: NodeType) -> "ParserEnvironment":
#         """
#         context manager - push a frame
#         """
#         self._pushframe(nodetype=nodetype)
#         return self

#     def __exit__(self, exc_type, exc_value, exc_traceback) -> None:
#         """
#         context manager - pop a frame
#         """
#         del self, exc_type, exc_value, exc_traceback
#         return

#     def _pushframe(
#         self, nodetype: NodeType, token: Optional[Token]
#     ) -> Optional[zast.Error]:
#         """
#         pushframe - push a FrameExpression (for Units or for Code)

#         nodetype = type of frame (based on the source NodeType)
#         token = first token of this frame (for error reporting if required)

#         Returns None if successful otherwise an error (pushing the wrong frame
#         type)
#         """
#         level = len(self.frames)
#         if level > 2:
#             cft = self.frames[-1].frametype
#             if nodetype == NodeType.UNIT and cft != NodeType.UNIT:
#                 msg = "Units may only be defined within Units."
#                 return zast.Error(err=ERR.BADUNIT, msg=msg, loc=token)
#             if nodetype == NodeType.FUNCTION and cft != NodeType.UNIT:
#                 # TODO: allow definitions at lower levels one day?
#                 msg = "Functions may only be defined within Units."
#                 return zast.Error(err=ERR.BADUNIT, msg=msg, loc=token)

#         self.frames.append(ParserFrame(frametype=nodetype))
#         return None  # success

#     def popframe(self) -> Union[ParserFrame, zast.Error]:
#         """
#         popframeexpr - pop the innermost frame off the environment stack. It
#             must be a FrameExpression

#         Returns the frame or an error if trying to pop the builtin frame or
#         a frame of the incorrect type or if there are unresolved forward
#         references (which should have been defined or promoted).
#         """
#         if len(self.frames) <= 1:
#             msg = "Cannot pop builtins. Compiler error."
#             return zast.Error(err=ERR.COMPILERERROR, msg=msg, loc=None)
#         frame = self.frames[-1]
#         if not isinstance(frame, ParserFrame):
#             msg = "Wrong frame type for pop. Expected FrameExpression"
#             return zast.Error(err=ERR.COMPILERERROR, msg=msg, loc=None)
#         if len(frame.refs) > 0:
#             msg = "There are unresolved references when popping a frame"
#             return zast.Error(err=ERR.COMPILERERROR, msg=msg, loc=None)

#         self.frames.pop()
#         return frame


# class ParserEnvironmentDELETEME:
#     """
#     ParserEnvironment - lexical environment for the Parser for tracking
#     definitions and forward references.

#     Unfortunately, need to handle 2 types of frames for those that store
#     Expressions (top level and code) and Operations (arguments, parameters,
#     calls etc). Why?
#     """

#     def __init__(self, systemtoken: Token) -> None:
#         """
#         systemtoken = the EOF token in the dummy system.z module file
#         """
#         self.frames: List[ParserFrame] = []
#         # core -- cannot be popped
#         self._pushframe(NodeType.UNIT, token=systemtoken)
#         b = zast.System(start=systemtoken)
#         d = zast.Definition(
#             start=systemtoken, name="system", expression=b, context=Context.NORMAL
#         )
#         # TODO: load the builtins from system.z
#         self.addbuiltin(d)
#         # global unit - can only be popped at end of program
#         # don't do this... create the unit with context manager in acceptprogram()
#         # self._pushframe(NodeType.UNIT, token=None)

#     def __enter__(self, nodetype: NodeType, token: Token):
#         """
#         context manager - push a frame
#         """
#         pass

#     def __exit__(self, exc_type, exc_value, exc_traceback):
#         del self, exc_type, exc_value, exc_traceback
#         return

#     def addreference(self, ref: Token) -> None:
#         """
#         addreference - search for a symbol and update 'ref's definition to
#         refer to it if it exists. If the definition does not yet exist, store
#         this AtomId and update it when the definition becomes available

#         Look up the call stack to find a referenced symbol (but only so far as
#         the innermost unit.

#         Nothing is returned.
#         """
#         name = ref.token
#         for f in reversed(self.frames):
#             if name in f.defs:
#                 return None  # found it, already defined

#             if f.frametype == NodeType.UNIT:
#                 # it is a unit, add this to refs
#                 if name not in f.refs:
#                     f.refs[name] = ref
#                 return None

#         # this cannot happen, need at least one top level unit in the
#         # environment
#         raise Exception("No Unit in environment")

#     def popref(self) -> Token:
#         """
#         popref - return (and remove) the last forward ref at the innermost
#         level. (Last as in FIFO). This should be the next name to attempt
#         to parse for a unit. This should only be called at a Unit level.

#         Will raise KeyError if no more undefined references.

#         Will raise an Exception if not at a Unit level.
#         """
#         f = self.frames[-1]
#         if f.frametype != NodeType.UNIT:
#             # Shouldn't happen. Error in Parser
#             raise Exception("popref() must only be called on a UNIT")
#         i = f.refs.popitem()  # will throw KeyError if empty
#         return i[1]

#     def _promote(self, name: str) -> Optional[zast.Error]:
#         """
#         promote - push responsibility for the resolution of a ref to the next
#             higher unit. Once a unit is fully parsed, all undefined refs
#             should be defined or promoted.

#         Returns None if successful, error otherwise. Error could be REFNOTFOUND
#         if not found after promotion all the way up to builtins.
#         """
#         sourcelevel = len(self.frames)  # level is 1-indexed
#         sourceframe = self.frames[-1]
#         destframe = self.frames[-2]
#         if (
#             sourceframe.frametype != NodeType.UNIT
#             or destframe.frametype != NodeType.UNIT
#         ):
#             # Shouldn't happen. Error in Parser. Assumes only units within units
#             msg = f"Cannot promote '{name}'. Source and Dest must be Units"
#             return zast.Error(err=ERR.COMPILERERROR, msg=msg, loc=None)
#         # remove from source, we will promote or errorout from here
#         token = sourceframe.refs.pop(name)
#         if name in destframe.defs:
#             return None  # successfully resolved in destframe

#         if sourcelevel == 2:
#             # it was not found in destination and destination is 'builtins'
#             # return error on the first reference
#             msg = f"Definition of identifier '{name}' could not be found"
#             return zast.Error(err=ERR.REFNOTFOUND, msg=msg, loc=token)
#         if name not in destframe.refs:
#             destframe.refs[name] = token
#         return None  # successfully promoted (but unresolved) in destframe

#     def adddefinition(self, definition: Token) -> Optional[zast.Error]:
#         """
#         adddefinition - add a new definition in the outermost frame.
#         """
#         return self._adddefinition(self.frames[-1], definition)

#     def addbuiltin(self, definition: Token) -> Optional[zast.Error]:
#         """
#         addbuiltin - add a new named expression to the builtin frame (frame 0)

#         Any forward reference to this name will be resolved/updated.

#         Returns None if successful otherwise an error (duplicate definition).
#         """
#         return self._adddefinition(self.frames[0], definition)

#     @staticmethod
#     def _adddefinition(frame: ParserFrame, definition: Token) -> Optional[zast.Error]:
#         """
#         _adddefinition - add a new name in the specified frame.
#         """
#         name = definition.token
#         if name in frame.defs:
#             msg = f"Duplicate definition of '{name}'"
#             return zast.Error(err=ERR.DUPLICATEDEF, msg=msg, loc=definition)

#         frame.defs[name] = None

#         # resolve any forward refs
#         if name in frame.refs:
#             del frame.refs[name]

#         return None  # success

#     def _pushframe(
#         self, nodetype: NodeType, token: Optional[Token]
#     ) -> Optional[zast.Error]:
#         """
#         pushframe - push a FrameExpression (for Units or for Code)

#         nodetype = type of frame (based on the source NodeType)
#         token = first token of this frame (for error reporting if required)

#         Returns None if successful otherwise an error (pushing the wrong frame
#         type)
#         """
#         level = len(self.frames)
#         if level > 2:
#             cft = self.frames[-1].frametype
#             if nodetype == NodeType.UNIT and cft != NodeType.UNIT:
#                 msg = "Units may only be defined within Units."
#                 return zast.Error(err=ERR.BADUNIT, msg=msg, loc=token)
#             if nodetype == NodeType.FUNCTION and cft != NodeType.UNIT:
#                 # TODO: allow definitions at lower levels one day?
#                 msg = "Functions may only be defined within Units."
#                 return zast.Error(err=ERR.BADUNIT, msg=msg, loc=token)

#         self.frames.append(ParserFrame(frametype=nodetype))
#         return None  # success

#     def popframe(self) -> Union[ParserFrame, zast.Error]:
#         """
#         popframeexpr - pop the innermost frame off the environment stack. It
#             must be a FrameExpression

#         Returns the frame or an error if trying to pop the builtin frame or
#         a frame of the incorrect type or if there are unresolved forward
#         references (which should have been defined or promoted).
#         """
#         if len(self.frames) <= 1:
#             msg = "Cannot pop builtins. Compiler error."
#             return zast.Error(err=ERR.COMPILERERROR, msg=msg, loc=None)
#         frame = self.frames[-1]
#         if not isinstance(frame, ParserFrame):
#             msg = "Wrong frame type for pop. Expected FrameExpression"
#             return zast.Error(err=ERR.COMPILERERROR, msg=msg, loc=None)
#         if len(frame.refs) > 0:
#             msg = "There are unresolved references when popping a frame"
#             return zast.Error(err=ERR.COMPILERERROR, msg=msg, loc=None)

#         self.frames.pop()
#         return frame

# def _addbuiltins(self) -> None:
#     """
#     addbuiltins - add all of the compiler built-in types
#     """
#     # signed integer types

#     self.addbuiltin(self.makebuiltin("i8", "i8"))
#     self.addbuiltin(self.makebuiltin("i16", "i16"))
#     self.addbuiltin(self.makebuiltin("i32", "i32"))
#     self.addbuiltin(self.makebuiltin("i64", "i64"))
#     self.addbuiltin(self.makebuiltin("i128", "i128"))

#     # unsigned integer types

#     self.addbuiltin(self.makebuiltin("u8", "u8"))
#     self.addbuiltin(self.makebuiltin("u16", "u16"))
#     self.addbuiltin(self.makebuiltin("u32", "u32"))
#     self.addbuiltin(self.makebuiltin("u64", "u64"))
#     self.addbuiltin(self.makebuiltin("u128", "u128"))

#     # floating point types
#     self.addbuiltin(self.makebuiltin("f32", "f32"))
#     self.addbuiltin(self.makebuiltin("f64", "f64"))

#     # functions
#     self.addbuiltin(self.makebuiltin("print", "io.print"))

# @staticmethod
# def makebuiltin(name: str, builtin: str) -> Definition:
#     """
#     makebuiltin - utility function to make a builtin Definition

#     Returns a Definition
#     """
#     b = Builtin(start=None, builtin=builtin)
#     d = Definition(start=None, name=name, expression=b, context=Context.BUILTIN)
#     return d
