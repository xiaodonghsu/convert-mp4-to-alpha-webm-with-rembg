from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable

from video_webm_app.pipeline import ConversionConfig, ConversionError, VideoConverter


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("MP4 to WebM with rembg")
        self.root.geometry("760x520")
        self.root.minsize(680, 480)

        self._events: queue.Queue[tuple[str, object]] = queue.Queue()
        self._worker: threading.Thread | None = None

        self.input_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.temp_var = tk.StringVar()
        self.keep_temp_var = tk.BooleanVar(value=False)
        self.crf_var = tk.IntVar(value=28)
        self.model_var = tk.StringVar(value="u2net")
        self.status_var = tk.StringVar(value="Ready")
        self.progress_var = tk.DoubleVar(value=0.0)

        self._build_ui()
        self.root.after(120, self._process_events)

    def _build_ui(self) -> None:
        frame = ttk.Frame(self.root, padding=18)
        frame.pack(fill="both", expand=True)

        ttk.Label(
            frame,
            text="Convert MP4 to transparent WebM with rembg (CPU)",
            font=("Segoe UI", 15, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            frame,
            text="Workflow: MP4 -> PNG frames + audio -> rembg -> WebM (VP9 + alpha)",
        ).pack(anchor="w", pady=(6, 18))

        self._build_path_row(frame, "Input MP4", self.input_var, self._pick_input)
        self._build_path_row(frame, "Output WebM", self.output_var, self._pick_output)
        self._build_path_row(frame, "Temp Directory", self.temp_var, self._pick_temp, optional=True)

        options = ttk.LabelFrame(frame, text="Options", padding=12)
        options.pack(fill="x", pady=(8, 14))
        options.columnconfigure(1, weight=1)
        options.columnconfigure(3, weight=1)

        ttk.Label(options, text="rembg model").grid(row=0, column=0, sticky="w")
        ttk.Entry(options, textvariable=self.model_var).grid(row=0, column=1, sticky="ew", padx=(8, 18))
        ttk.Label(options, text="VP9 CRF").grid(row=0, column=2, sticky="w")
        ttk.Spinbox(options, from_=18, to=40, textvariable=self.crf_var, width=8).grid(
            row=0, column=3, sticky="w", padx=(8, 0)
        )
        ttk.Checkbutton(options, text="Keep temporary files", variable=self.keep_temp_var).grid(
            row=1, column=0, columnspan=4, sticky="w", pady=(10, 0)
        )

        actions = ttk.Frame(frame)
        actions.pack(fill="x")
        self.start_button = ttk.Button(actions, text="Start Conversion", command=self._start_conversion)
        self.start_button.pack(side="left")
        ttk.Button(actions, text="Fill Output Name", command=self._fill_output_name).pack(side="left", padx=(10, 0))

        ttk.Progressbar(frame, maximum=100, variable=self.progress_var).pack(fill="x", pady=(16, 8))
        ttk.Label(frame, textvariable=self.status_var).pack(anchor="w")

        self.log_text = tk.Text(frame, height=14, wrap="word", state="disabled")
        self.log_text.pack(fill="both", expand=True, pady=(12, 0))

    def _build_path_row(
        self,
        parent: ttk.Frame,
        label: str,
        variable: tk.StringVar,
        callback: Callable[[], None],
        optional: bool = False,
    ) -> None:
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(0, 10))
        ttk.Label(row, text=label, width=12).pack(side="left")
        ttk.Entry(row, textvariable=variable).pack(side="left", fill="x", expand=True, padx=(8, 8))
        ttk.Button(row, text="Browse", command=callback).pack(side="left")
        if optional:
            ttk.Label(row, text="Optional").pack(side="left", padx=(8, 0))

    def _pick_input(self) -> None:
        selected = filedialog.askopenfilename(
            title="Select MP4 file",
            filetypes=[("MP4 video", "*.mp4"), ("All files", "*.*")],
        )
        if not selected:
            return
        self.input_var.set(selected)
        if not self.output_var.get():
            output_path = Path(selected).with_suffix(".webm")
            self.output_var.set(str(output_path))

    def _pick_output(self) -> None:
        selected = filedialog.asksaveasfilename(
            title="Save WebM as",
            defaultextension=".webm",
            filetypes=[("WebM video", "*.webm")],
        )
        if selected:
            self.output_var.set(selected)

    def _pick_temp(self) -> None:
        selected = filedialog.askdirectory(title="Select temporary directory")
        if selected:
            self.temp_var.set(selected)

    def _fill_output_name(self) -> None:
        if not self.input_var.get():
            messagebox.showinfo("Missing input", "Select an MP4 file first.")
            return
        self.output_var.set(str(Path(self.input_var.get()).with_suffix(".webm")))

    def _start_conversion(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return

        input_path = Path(self.input_var.get()).expanduser()
        output_path = Path(self.output_var.get()).expanduser()
        temp_root = Path(self.temp_var.get()).expanduser() if self.temp_var.get().strip() else None

        if not input_path.exists():
            messagebox.showerror("Input error", "Input MP4 file does not exist.")
            return
        if input_path.suffix.lower() != ".mp4":
            messagebox.showerror("Input error", "Input file must be an MP4 video.")
            return
        if output_path.suffix.lower() != ".webm":
            messagebox.showerror("Output error", "Output file must use the .webm extension.")
            return

        config = ConversionConfig(
            input_path=input_path,
            output_path=output_path,
            temp_root=temp_root,
            keep_temp=self.keep_temp_var.get(),
            crf=self.crf_var.get(),
            model_name=self.model_var.get().strip() or "u2net",
        )

        self._set_busy(True)
        self._append_log(f"Starting conversion for {input_path}")
        self.status_var.set("Working")
        self.progress_var.set(0.0)

        def worker() -> None:
            converter = VideoConverter(
                status_callback=lambda message: self._events.put(("log", message)),
                progress_callback=lambda value, message: self._events.put(("progress", (value, message))),
            )
            try:
                output = converter.convert(config)
            except Exception as exc:
                self._events.put(("error", exc))
                return
            self._events.put(("done", output))

        self._worker = threading.Thread(target=worker, daemon=True)
        self._worker.start()

    def _process_events(self) -> None:
        while True:
            try:
                event, payload = self._events.get_nowait()
            except queue.Empty:
                break

            if event == "log":
                self._append_log(str(payload))
            elif event == "progress":
                value, message = payload
                self.progress_var.set(float(value) * 100)
                self.status_var.set(str(message))
            elif event == "error":
                self._set_busy(False)
                self.status_var.set("Failed")
                error = payload if isinstance(payload, Exception) else RuntimeError(str(payload))
                self._append_log(f"ERROR: {error}")
                messagebox.showerror("Conversion failed", str(error))
            elif event == "done":
                self._set_busy(False)
                self.progress_var.set(100.0)
                self.status_var.set("Completed")
                self._append_log(f"Completed: {payload}")
                messagebox.showinfo("Conversion completed", f"Output saved to:\n{payload}")

        self.root.after(120, self._process_events)

    def _set_busy(self, busy: bool) -> None:
        self.start_button.config(state="disabled" if busy else "normal")

    def _append_log(self, message: str) -> None:
        self.log_text.config(state="normal")
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")
        self.log_text.config(state="disabled")


def launch_app() -> None:
    root = tk.Tk()
    try:
        root.iconname("rembg-webm")
    except tk.TclError:
        pass
    App(root)
    root.mainloop()
