"""受限子进程入口。用户程序只交给 WASI 内部的 CPython 解释器。"""
from hashlib import sha256
from importlib.metadata import version
import json
from pathlib import Path
import sys
from threading import Timer

if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from sandbox_file_access import FileAccess
else:
    from .sandbox_file_access import FileAccess

PYTHON_WASM_SHA256 = "19165271f96e2067e5d07e315500202f5a257a5055fbdc3611239abad82f0844"
WASMTIME_VERSION = "50.0.1"
OUTPUT_LIMIT = 16 * 1024


def execute(runtime: Path, job: Path, filename: str | None = None) -> dict:
    from wasmtime import Config, Engine, ExitTrap, Linker, Module, Store, Trap, TrapCode, WasiConfig

    library = runtime / "lib"
    if library.resolve() != library:
        raise ValueError("redirected WASI library")
    with (runtime / "python.wasm").open("rb") as wasm:
        module_bytes = wasm.read(30_000_001)
    if len(module_bytes) > 30_000_000 or sha256(module_bytes).hexdigest() != PYTHON_WASM_SHA256:
        raise ValueError("unexpected WASI interpreter")
    if version("wasmtime") != WASMTIME_VERSION:
        raise ValueError("unsupported Wasmtime version")

    config = Config()
    config.consume_fuel = True
    config.epoch_interruption = True
    config.cranelift_opt_level = "none"
    engine = Engine(config)
    module = Module(engine, module_bytes)
    if any(item.module != "wasi_snapshot_preview1" for item in module.imports):
        raise ValueError("unexpected host import")

    stdout, stderr = bytearray(), bytearray()
    output_limited = False

    def output(buffer, data):
        nonlocal output_limited
        remaining = OUTPUT_LIMIT - len(stdout) - len(stderr)
        buffer.extend(data[:remaining])
        if len(data) > remaining and not output_limited:
            output_limited = True
            engine.increment_epoch()
        return len(data)

    wasi = WasiConfig()
    wasi.argv = ["python", "-B", "-S", "-P", "program.py"]
    environment = [("PYTHONHOME", "/runtime"), ("PYTHONIOENCODING", "utf-8"), ("VISIONSOUL_SANDBOX", "1")]
    if filename:
        environment.append(("VISIONSOUL_DATA_FILE", filename))
    wasi.env = environment
    wasi.preopen_dir(str(library), "/runtime/lib", fs_mutable=False)
    wasi.preopen_dir(str(job / "guest"), ".", fs_mutable=False)
    wasi.stdin_file = str(job / "stdin.txt")
    wasi.stdout_custom = lambda data: output(stdout, data)
    wasi.stderr_custom = lambda data: output(stderr, data)
    store = Store(engine)
    store.set_limits(memory_size=192 * 1024 * 1024, instances=2, tables=2, memories=1)
    store.set_fuel(1_500_000_000)
    store.set_epoch_deadline(1)
    store.set_wasi(wasi)
    linker = Linker(engine)
    linker.define_wasi()
    # WASI assigns the preopens in order, starting after stdin, stdout and stderr.
    access = FileAccess(store, linker, filename, guest_fd=4, python_runtime=True)

    limit, code = None, 0
    timer = Timer(4, engine.increment_epoch)
    timer.daemon = True
    timer.start()
    try:
        exports = linker.instantiate(store, module).exports(store)
        if "memory" in exports:
            access.attach(engine, exports)
        exports["_start"](store)
    except ExitTrap as error:
        code = error.code
    except Trap as error:
        code = None
        if error.trap_code in {TrapCode.OUT_OF_FUEL, TrapCode.INTERRUPT, TrapCode.MEMORY_OUT_OF_BOUNDS}:
            limit = "resource"
        else:
            limit = "trap"
    finally:
        timer.cancel()
        timer.join()
    if output_limited:
        limit = "output"
    return {"exit_code": code, "limit": limit, "stdout": stdout.decode("utf-8", "replace"),
            "stderr": stderr.decode("utf-8", "replace"),
            "file_read": access.file_read, "missing_file": access.missing_file, "denied_write": access.denied_write}


def main():
    try:
        result = execute(Path(sys.argv[1]), Path(sys.argv[2]), *sys.argv[3:])
    except Exception:
        result = {"unavailable": True}
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
