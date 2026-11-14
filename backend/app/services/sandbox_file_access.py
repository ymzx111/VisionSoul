"""Observe file reads without granting any capability beyond the WASI mounts."""
from wasmtime import FuncType, Instance, Module, Trap, ValType

NAMESPACE = "wasi_snapshot_preview1"
FORWARDED = {
    "path_open": "iiiiiIIii",
    "fd_read": "iiii",
    "fd_pread": "iiiIi",
    "fd_close": "i",
    "fd_renumber": "ii",
}


class FileAccess:
    def __init__(self, store, linker, filename, *, guest_fd, python_runtime=False, stop_at_stdin=False):
        self.store = store
        self.filename = filename.encode("utf-8") if filename else None
        self.guest_fd = guest_fd
        self.python_runtime = python_runtime
        self.stop_at_stdin = stop_at_stdin
        self.awaiting_input = False
        self.file_read = False
        self.missing_file = False
        self.descriptors = set()
        self.memory = None
        self.originals = []
        self.forwarders = None
        linker.allow_shadowing = True
        for name, params in FORWARDED.items():
            self.originals.append(linker.get(store, NAMESPACE, name))
            signature = FuncType([ValType.i64() if p == "I" else ValType.i32() for p in params], [ValType.i32()])
            linker.define_func(NAMESPACE, name, signature, self.wrap(name))

    def attach(self, engine, exports):
        self.memory = exports["memory"]
        imports, functions = [], []
        for name, params in FORWARDED.items():
            args = " ".join("i64" if p == "I" else "i32" for p in params)
            values = " ".join(f"local.get {index}" for index in range(len(params)))
            imports.append(f'(import "wasi" "{name}" (func $f_{name} (param {args}) (result i32)))')
            functions.append(f'(func (export "{name}") (param {args}) (result i32) {values} call $f_{name})')
        adapter = Module(engine, "(module (import \"guest\" \"memory\" (memory 0)) "
                         + " ".join(imports) + ' (export "memory" (memory 0)) ' + " ".join(functions) + ")")
        self.forwarders = Instance(self.store, adapter, [self.memory, *self.originals]).exports(self.store)

    def data(self, address, length):
        if address < 0 or length < 0 or address + length > self.memory.data_len(self.store):
            return b""
        return bytes(self.memory.read(self.store, address, address + length))

    def wrap(self, name):
        def call(*args):
            fd = args[0]
            if name == "fd_read" and fd == 0 and args[2] > 0 and self.stop_at_stdin:
                self.awaiting_input = True
                raise Trap("stdin was not supplied")
            status = self.forwarders[name](self.store, *args)
            if name == "path_open":
                path = self.data(args[2], args[3]) if 0 < args[3] <= 4096 else b""
                normalized = path.replace(b"\\", b"/")
                relative = normalized
                while relative.startswith(b"./"):
                    relative = relative[2:]
                if status == 0:
                    target = int.from_bytes(self.data(args[-1], 4), "little")
                    if fd == self.guest_fd and relative == self.filename:
                        self.descriptors.add(target)
                elif (fd == self.guest_fd and status in {2, 44, 63, 76}
                      and normalized.lower().endswith((b".txt", b".csv", b".json"))
                      and not (self.python_runtime and relative == b"pybuilddir.txt")):
                    # No host path or file content is reported outside this worker.
                    self.missing_file = True
            elif status == 0 and name in {"fd_read", "fd_pread"} and fd in self.descriptors:
                self.file_read |= int.from_bytes(self.data(args[-1], 4), "little") > 0
            elif status == 0 and name == "fd_close":
                self.descriptors.discard(fd)
            elif status == 0 and name == "fd_renumber":
                target = args[1]
                tracked = fd in self.descriptors
                self.descriptors.discard(fd)
                self.descriptors.discard(target)
                if tracked:
                    self.descriptors.add(target)
            return status
        return call
