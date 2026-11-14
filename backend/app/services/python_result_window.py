"""Display sandbox results and submit input to the local sandbox service."""
import ctypes
from ctypes import wintypes as w
import json
from pathlib import Path
from queue import Empty, SimpleQueue
import sys
from threading import Thread
import tkinter as tk
from tkinter import filedialog, font, ttk
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


def arrange(root, source, language, attempts=0):
    user = ctypes.WinDLL("user32")
    user.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
    user.IsWindowVisible.argtypes = [w.HWND]
    user.ShowWindow.argtypes = [w.HWND, ctypes.c_int]
    user.MoveWindow.argtypes = [w.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, w.BOOL]
    editor = None
    callback = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)

    @callback
    def find_editor(window, _):
        nonlocal editor
        if not user.IsWindowVisible(window):
            return True
        title = ctypes.create_unicode_buffer(4096)
        user.GetWindowTextW(window, title, len(title))
        caption = title.value.casefold()
        if language == "Python":
            matches = source.casefold() in caption and "(3." in caption
        else:
            matches = Path(source).name.casefold() in caption and "visual studio" in caption
        if matches:
            editor = window
            return False
        return True

    user.EnumWindows(find_editor, 0)
    if not editor and attempts < 80:
        root.after(100, lambda: arrange(root, source, language, attempts + 1))
        if attempts:
            return

    class MonitorInfo(ctypes.Structure):
        _fields_ = [("size", w.DWORD), ("monitor", w.RECT), ("work", w.RECT), ("flags", w.DWORD)]

    user.MonitorFromWindow.argtypes = [w.HWND, w.DWORD]
    user.MonitorFromWindow.restype = w.HANDLE
    user.GetMonitorInfoW.argtypes = [w.HANDLE, ctypes.POINTER(MonitorInfo)]
    info = MonitorInfo(ctypes.sizeof(MonitorInfo))
    monitor = user.MonitorFromWindow(editor, 1)
    if user.GetMonitorInfoW(monitor, ctypes.byref(info)):
        area = info.work
        width, height = area.right - area.left, area.bottom - area.top
        left = round(width * 0.53)
        if editor:
            user.ShowWindow(editor, 9)  # Restore a maximized editor before moving it.
            user.MoveWindow(editor, area.left, area.top, left, height, True)
        # Native positioning uses absolute monitor coordinates, including monitors left of the primary.
        root.update_idletasks()
        user.GetParent.argtypes = [w.HWND]
        user.GetParent.restype = w.HWND
        window = user.GetParent(root.winfo_id()) or root.winfo_id()
        user.MoveWindow(window, area.left + left, area.top, width - left, height, True)
    root.deiconify()
    root.lift()


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def submit_input(origin, run_id, stdin, approved_file=None):
    local = urlsplit(origin)
    if (local.scheme not in {"http", "https"} or local.hostname not in {"127.0.0.1", "localhost", "::1"}
            or local.username is not None or local.path or local.query or local.fragment):
        raise ValueError("只能连接本机 VisionSoul 服务。")
    payload = {"run_id": run_id, "stdin": stdin, "open_result_window": False}
    if approved_file is not None:
        payload["approved_file"] = approved_file
    body = json.dumps(payload).encode("utf-8")
    request = Request(origin + "/api/chat/sandbox-runs", body, {
        "Origin": origin, "Content-Type": "application/json",
    })
    try:
        with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=90) as response:
            return json.load(response)
    except HTTPError as exc:
        with exc:
            try:
                detail = json.load(exc).get("detail")
            except (ValueError, AttributeError):
                detail = None
        if isinstance(detail, list) and any("approved_file" in error.get("loc", []) for error in detail):
            raise ValueError("文件名不符合本机试跑要求，请用字母、数字、中文、空格或连接号命名后重新选择。") from exc
        raise ValueError(detail if isinstance(detail, str) else "试跑请求无效，请检查输入。") from exc
    except (URLError, TimeoutError) as exc:
        raise ValueError("无法连接本机试跑服务，请确认工作台仍在运行。") from exc


def output_panel(parent, mono):
    panel = ttk.Frame(parent)
    panel.pack(fill="both", expand=True)
    text = tk.Text(panel, background="#ffffff", foreground="#0000b3", font=(mono, 12),
                   wrap="word", padx=12, pady=8, relief="flat", undo=False)
    bar = ttk.Scrollbar(panel, command=text.yview)
    bar.pack(side="right", fill="y")
    text.configure(yscrollcommand=bar.set)
    text.pack(side="left", fill="both", expand=True)
    text.tag_configure("label", foreground="#4d4d4d")
    text.tag_configure("error", foreground="#a32222")
    text.configure(state="disabled")
    return text


def show_result(text, result):
    text.configure(state="normal")
    text.delete("1.0", "end")
    if result["status"] in {"awaiting_file", "file_unused"}:
        text.insert("end", result["detail"], "label")
        text.configure(state="disabled")
        return
    cases = result["case_results"] or []
    rows = cases or [result]
    if cases and result["status"] == "awaiting_input":
        rows = [*cases, result]
    for index, row in enumerate(rows):
        if row is not result:
            state = {"passed": "通过", "ran": "已试跑"}.get(row["status"], "未通过")
            text.insert("end", f"{row['name']} · {state}\n", "label")
        stdin = row.get("stdin")
        if row["status"] == "awaiting_input":
            text.insert("end", "等待输入，请填写本次试跑的标准输入。\n", "label")
        if stdin is not None and row.get("phase") != "compile" and (stdin or row["status"] != "awaiting_input"):
            text.insert("end", "标准输入\n", "label")
            text.insert("end", stdin or "（空输入）", "label")
            if not stdin.endswith("\n"):
                text.insert("end", "\n")
        text.insert("end", "程序输出\n", "label")
        text.insert("end", row["stdout"] or "（无输出）")
        if row["stderr"] and row["status"] != "awaiting_input":
            text.insert("end", "\n" + row["stderr"], "error")
        if index < len(rows) - 1:
            text.insert("end", "\n\n")
    text.configure(state="disabled")


def add_manual_run(root, tabs, initial, mono):
    page = ttk.Frame(tabs, padding=8)
    tabs.add(page, text="手动试跑")
    ttk.Label(page, text="标准输入（按题目格式填写，可分多行）").pack(anchor="w")
    entry = tk.Text(page, height=4, wrap="word", font=(mono, 11), undo=True)
    entry.pack(fill="x", pady=(4, 6))
    controls = ttk.Frame(page)
    controls.pack(fill="x", pady=(0, 6))
    selected_file = None
    needs_file = initial["status"] in {"awaiting_file", "file_unused"} or bool(initial.get("data_file"))
    status = tk.StringVar(value="填写数据或选择本轮只读文件后运行。")
    file_label = tk.StringVar(value="未选择文件。请选择本轮允许程序读取的数据文件。")

    def choose_file():
        nonlocal selected_file
        picked = filedialog.askopenfilename(parent=root, title="选择本轮只读数据文件",
                                             filetypes=[("数据文件", "*.txt *.csv *.json")])
        if not picked:
            return
        path = Path(picked)
        try:
            if path.suffix.lower() not in {".txt", ".csv", ".json"}:
                raise ValueError("请选择 TXT、CSV 或 JSON 文件。")
            with path.open("rb") as file:
                data = file.read(8193)
            if len(data) > 8192:
                raise ValueError("数据文件超过 8 KiB，请选择较小的文件。")
            selected_file = {"filename": path.name, "content": data.decode("utf-8-sig")}
        except (ValueError, OSError) as exc:
            selected_file = None
            if isinstance(exc, UnicodeDecodeError):
                file_label.set("文件不是 UTF-8 编码，请另存为 UTF-8 后再选择。")
            elif isinstance(exc, OSError):
                file_label.set("本机文件读取失败，请确认文件仍在原位置。")
            else:
                file_label.set(str(exc))
            return
        file_label.set(f"本轮文件：{path.name}")

    file_button = ttk.Button(controls, text="选择数据文件", command=choose_file)
    file_button.pack(side="left", padx=(0, 6))
    ttk.Label(page, textvariable=file_label, wraplength=450).pack(anchor="w")
    ttk.Label(controls, textvariable=status, wraplength=380).pack(side="left", fill="x", expand=True)
    output = output_panel(page, mono)
    responses = SimpleQueue()

    def receive():
        nonlocal needs_file
        try:
            result, error = responses.get_nowait()
        except Empty:
            root.after(80, receive)
            return
        entry.configure(state="normal")
        button.configure(state="normal")
        file_button.configure(state="normal")
        if error is not None:
            status.set(error)
            return
        filename = result.get("data_file")
        needs_file = needs_file or bool(filename) or result["status"] in {"awaiting_file", "file_unused"}
        button.configure(text="授权并试跑" if needs_file else "运行")
        status.set(f"实际读取：{filename} · {result['detail']}" if filename else result["detail"])
        show_result(output, result)

    def run():
        nonlocal selected_file
        if needs_file and selected_file is None:
            status.set("请先从数据所在目录选择本轮要读取的文件。")
            return
        stdin = entry.get("1.0", "end-1c")
        if stdin and not stdin.endswith("\n"):
            stdin += "\n"
        if len(stdin) > 4096:
            status.set("标准输入最多 4096 个字符。")
            return
        approved_file, selected_file = selected_file, None
        file_label.set("下次试跑请重新选择文件。")
        entry.configure(state="disabled")
        button.configure(state="disabled")
        file_button.configure(state="disabled")
        status.set("正在沙箱试跑…")

        def worker():
            try:
                responses.put((submit_input(initial["origin"], initial["run_id"], stdin, approved_file), None))
            except (ValueError, OSError) as exc:
                responses.put((None, str(exc)))

        Thread(target=worker, daemon=True).start()
        root.after(80, receive)

    button = ttk.Button(controls, text="授权并试跑" if needs_file else "运行", command=run)
    button.pack(side="right")
    if initial["status"] in {"awaiting_input", "awaiting_file", "file_unused"}:
        tabs.select(page)
        status.set(initial["detail"])
        show_result(output, initial)
        root.after_idle(file_button.focus_set if needs_file else entry.focus_set)


def main():
    with sys.stdin.buffer as stream:
        result = json.load(stream)
    root = tk.Tk()
    root.withdraw()
    root.title(f"VisionSoul 沙箱输出 — {result['name']}")
    root.geometry("560x530")
    root.minsize(320, 260)
    language = result.get("language", "Python")
    ttk.Label(root, text=f"{language} 沙箱输出", padding=(12, 8)).pack(anchor="w")
    mono = "Consolas" if "Consolas" in font.families(root) else "Courier New"
    tabs = ttk.Notebook(root)
    tabs.pack(fill="both", expand=True, padx=6, pady=(0, 6))
    automatic = ttk.Frame(tabs)
    tabs.add(automatic, text="AI 自测" if result.get("case_source") == "model" else "我的数据" if result.get("data_file") else "首次运行")
    if result.get("data_file"):
        ttk.Label(automatic, text=f"实际读取：{result['data_file']}", padding=(8, 6)).pack(anchor="w")
    ttk.Label(automatic, text=result["detail"], padding=(8, 6), wraplength=475).pack(anchor="w")
    show_result(output_panel(automatic, mono), result)
    if result.get("origin") and result.get("run_id"):
        add_manual_run(root, tabs, result, mono)
    arrange(root, result["editor_path"], language)
    root.mainloop()


if __name__ == "__main__":
    main()
