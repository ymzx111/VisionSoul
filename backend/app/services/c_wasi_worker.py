"""受限子进程入口。C 程序先由宿主机编译成 wasm，再只交给 Wasmtime 运行。"""
from importlib.metadata import version
import json
from pathlib import Path
import sys
from threading import Timer

WASMTIME_VERSION = "50.0.1"
OUTPUT_LIMIT = 16 * 1024
MAX_MODULE = 8 * 1024 * 1024


def execute(module_path: Path, job: Path) -> dict:
    from wasmtime import Config, Engine, ExitTrap, Linker, Module, Store, Trap, TrapCode, WasiConfig

    if module_path.is_symlink():
        raise ValueError("redirected module")
    with module_path.open("rb") as handle:
        module_bytes = handle.read(MAX_MODULE + 1)
    if len(module_bytes) > MAX_MODULE:
        raise ValueError("module too large")
    if version("wasmtime") != WASMTIME_VERSION:
        raise ValueError("unsupported Wasmtime version")

    config = Config()
    config.consume_fuel = True
    config.epoch_interruption = True
    config.cranelift_opt_level = "none"
    engine = Engine(config)
    module = Module(engine, module_bytes)
    # 只允许标准 WASI 导入，出现其他宿主能力就直接拒绝运行。
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
    wasi.argv = ["program"]
    wasi.env = []
    wasi.preopen_dir(str(job / "guest"), ".", fs_mutable=False)
    wasi.stdin_file = str(job / "stdin.txt")
    wasi.stdout_custom = lambda data: output(stdout, data)
    wasi.stderr_custom = lambda data: output(stderr, data)

    store = Store(engine)
    store.set_limits(memory_size=192 * 1024 * 1024, instances=1, tables=2, memories=1)
    store.set_fuel(1_500_000_000)
    store.set_epoch_deadline(1)
    store.set_wasi(wasi)
    linker = Linker(engine)
    linker.define_wasi()

    limit, code = None, 0
    timer = Timer(4, engine.increment_epoch)
    timer.daemon = True
    timer.start()
    try:
        linker.instantiate(store, module).exports(store)["_start"](store)
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
            "stderr": stderr.decode("utf-8", "replace")}


def main():
    try:
        result = execute(Path(sys.argv[1]), Path(sys.argv[2]))
    except Exception:
        result = {"unavailable": True}
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
