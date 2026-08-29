import os
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from PIL import Image, ImageTk

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    HAS_DND = True
except ImportError:
    HAS_DND = False

# 界面显示名（含 JPG）-> PIL 内部格式名
SUPPORTED_FORMATS = ["PNG", "JPG", "BMP", "GIF", "TIFF", "WEBP", "ICO"]

_FORMAT_MAP = {
    "JPG": "JPEG",
    "PNG": "PNG",
    "BMP": "BMP",
    "GIF": "GIF",
    "TIFF": "TIFF",
    "WEBP": "WEBP",
    "ICO": "ICO",
}


def pil_format(display_name):
    """把界面显示名转成 PIL 认识的格式名。"""
    return _FORMAT_MAP.get(display_name, display_name)


def format_from_ext(path):
    ext = os.path.splitext(path)[1].lower()
    frmts = {
        ".png": "PNG", ".jpg": "JPG", ".jpeg": "JPG",
        ".bmp": "BMP", ".gif": "GIF", ".tiff": "TIFF",
        ".tif": "TIFF", ".webp": "WEBP", ".ico": "ICO",
    }
    return frmts.get(ext)


class ImageConverterApp:
    """第二版：简洁黑白（灰白）界面，稳定可靠，支持 JPG。"""

    def __init__(self, root):
        self.root = root
        root.title("图片格式转换器")
        # 默认尺寸 = 最小尺寸 = 第二张图的大小（完整显示预览区 + 底部下载按钮）
        root.geometry("760x680")
        root.minsize(760, 680)

        # ===== 黑白 / 灰白配色 =====
        self.bg_color = "#ffffff"        # 主背景：白
        self.panel_bg = "#f5f5f5"       # 面板：浅灰
        self.border_color = "#999999"   # 边框：中灰
        self.text_color = "#222222"     # 文字：近黑
        self.text_dim = "#666666"       # 次要文字
        self.button_bg = "#333333"      # 按钮：深灰（黑）
        self.button_fg = "#ffffff"      # 按钮文字：白

        self.input_path = None
        self.input_format = None
        self.preview_img = None
        self.converted_img = None
        self.converted_format = None

        self._setup_style()
        self.build_widgets()

    def _setup_style(self):
        style = ttk.Style()
        style.theme_use("clam")

        style.configure("TFrame", background=self.bg_color)
        style.configure("TLabel", background=self.bg_color, foreground=self.text_color)
        style.configure("TButton",
                        background=self.button_bg,
                        foreground=self.button_fg,
                        padding=(12, 6))
        style.map("TButton",
                  background=[("active", "#000000")],
                  foreground=[("active", "#ffffff")])
        style.configure("TCombobox",
                        fieldbackground="#ffffff",
                        background="#ffffff",
                        foreground=self.text_color)

        # 全局字体
        try:
            from tkinter import font as tkfont
            for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont"):
                tkfont.nametofont(name).configure(family="Microsoft YaHei", size=10)
        except Exception:
            pass

    def build_widgets(self):
        self.root.configure(bg=self.bg_color)

        main = ttk.Frame(self.root, padding=18)
        main.pack(fill="both", expand=True)

        # 标题
        tk.Label(main, text="图片格式转换器",
                 bg=self.bg_color, fg=self.text_color,
                 font=("Microsoft YaHei", 15, "bold")).pack(anchor="w", pady=(0, 12))

        # 拖拽区（Label + 灰色虚线边框效果，用 highlight 实现）
        self.drop_frame = tk.Label(
            main,
            text="把图片拖到这里\n支持 PNG / JPG / BMP / GIF / TIFF / WEBP / ICO",
            width=60, height=5,
            bg=self.panel_bg, fg=self.text_dim,
            font=("Microsoft YaHei", 10),
            relief="solid", bd=1,
            highlightthickness=2,
            highlightbackground=self.border_color,
            highlightcolor=self.border_color,
        )
        self.drop_frame.pack(fill="x", pady=(0, 12))

        if HAS_DND:
            self.drop_frame.drop_target_register(DND_FILES)
            self.drop_frame.dnd_bind("<<Drop>>", self.on_drop)

        # 文件信息
        self.info_var = tk.StringVar(value="未选择图片")
        tk.Label(main, textvariable=self.info_var,
                 bg=self.bg_color, fg=self.text_color,
                 font=("Microsoft YaHei", 10), anchor="w").pack(fill="x", pady=(0, 10))

        # 目标格式 + 转换按钮
        fmt_row = ttk.Frame(main)
        fmt_row.pack(fill="x", pady=(0, 12))

        tk.Label(fmt_row, text="目标格式：",
                 bg=self.bg_color, fg=self.text_color,
                 font=("Microsoft YaHei", 10)).pack(side="left")
        self.format_combo = ttk.Combobox(fmt_row, values=SUPPORTED_FORMATS,
                                         state="readonly", width=12)
        self.format_combo.set("JPG")
        self.format_combo.pack(side="left", padx=(5, 20))

        self.convert_btn = ttk.Button(fmt_row, text="转换并预览", command=self.convert)
        self.convert_btn.pack(side="left")

        # 预览区
        preview_frame = tk.LabelFrame(
            main, text=" 转换后的预览 ",
            bg=self.bg_color, fg=self.text_color,
            font=("Microsoft YaHei", 9),
            relief="solid", bd=1,
        )
        preview_frame.pack(fill="both", expand=True, pady=(0, 12))

        # 弹性占位：把底部按钮始终压在窗口最下方，避免被预览区挤出可视区域
        spacer = tk.Frame(main, bg=self.bg_color)
        spacer.pack(side="bottom", fill="y", expand=True)

        self.preview_label = tk.Label(
            preview_frame, text="预览区域",
            bg=self.panel_bg, fg=self.text_dim,
            width=58, height=12,
            font=("Microsoft YaHei", 10),
        )
        self.preview_label.pack(padx=8, pady=8, fill="both", expand=True)

        # 下载按钮（贴底，窗口缩放/锁定时始终可见）
        self.download_btn = ttk.Button(main, text="下载转换后的图片", command=self.download)
        self.download_btn.configure(state="disabled")
        self.download_btn.pack(side="bottom", pady=(15, 5), fill="x")

        # 底部状态栏（下载成功时静默提示，替代弹窗）
        self.status_var = tk.StringVar(value="")
        status_label = tk.Label(
            main, textvariable=self.status_var,
            bg=self.bg_color, fg="#2e7d32",
            font=("Microsoft YaHei", 9), anchor="w",
        )
        status_label.pack(side="bottom", fill="x", padx=2, pady=(0, 2))

    # ============ 功能逻辑（保持不变） ============

    def on_drop(self, event):
        raw = event.data
        if "{" in raw:
            import re
            matches = re.findall(r"\{([^{}]+)\}", raw)
            candidates = matches if matches else [raw]
        else:
            candidates = raw.split()

        for cand in candidates:
            path = cand.strip()
            if not path:
                continue
            if not os.path.isfile(path):
                messagebox.showerror("错误", f"文件不存在：\n{path}", parent=self.root)
                return
            self.load_image(path)
            self.format_combo.set("JPG")
            self.download_btn.configure(state="disabled")
            self.preview_label.configure(image="", text="预览区域")
            return

    def load_image(self, path):
        try:
            img = Image.open(path)
            img.verify()
        except Exception as e:
            messagebox.showerror("错误", f"无法识别图片：\n{e}", parent=self.root)
            return

        try:
            img = Image.open(path)  # verify 后需重新打开
        except Exception as e:
            messagebox.showerror("错误", f"无法读取图片：\n{e}", parent=self.root)
            return

        self.input_path = path
        fmt = format_from_ext(path) or (img.format or "未知")
        self.input_format = fmt
        self.info_var.set(f"{os.path.basename(path)}\n格式: {fmt}  |  尺寸: {img.width} × {img.height}")
        self.status_var.set("")  # 新图片载入，清空状态栏

    def convert(self):
        if not self.input_path:
            messagebox.showwarning("提示", "请先拖入一张图片。", parent=self.root)
            return

        target = pil_format(self.format_combo.get())  # JPG -> JPEG
        try:
            img = Image.open(self.input_path)
            img = self._make_rgba_compatible(img, target)
            self.converted_img = img
            self.converted_format = target
            self.show_preview(img)
            self.download_btn.configure(state="normal")
            self.status_var.set("")  # 新一轮转换，清空状态
        except Exception as e:
            messagebox.showerror("转换失败", str(e), parent=self.root)

    @staticmethod
    def _make_rgba_compatible(img, target):
        if target == "JPEG":  # 对应界面里的 JPG
            if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                background = Image.new("RGB", img.size, (255, 255, 255))
                rgba = img.convert("RGBA")
                background.paste(rgba, mask=rgba.split()[-1])
                return background
            return img.convert("RGB")
        if target == "ICO":
            return img.convert("RGBA")
        return img

    def show_preview(self, img):
        w, h = img.size
        ratio = min(500 / w, 300 / h, 1)
        new_size = (max(1, int(w * ratio)), max(1, int(h * ratio)))
        thumb = img.copy()
        thumb.thumbnail(new_size, Image.LANCZOS)
        self.preview_img = ImageTk.PhotoImage(thumb)
        self.preview_label.configure(image=self.preview_img, text="")
        self.preview_label.image = self.preview_img

    def download(self):
        if not self.input_path or self.converted_img is None:
            return
        ext = self.converted_format.lower()
        if self.converted_format == "JPEG":
            ext = "jpg"
        default_name = os.path.splitext(os.path.basename(self.input_path))[0] + f"_converted.{ext}"
        filetypes = [(f"{self.converted_format} 文件", f"*.{ext}"), ("所有文件", "*.*")]
        path = filedialog.asksaveasfilename(
            title="保存转换后的图片",
            initialfile=default_name,
            defaultextension=f".{ext}",
            filetypes=filetypes,
            parent=self.root,
        )
        if not path:
            return
        try:
            save_kwargs = {}
            if self.converted_format == "JPEG":
                save_kwargs["quality"] = 95
            if self.converted_format == "WEBP":
                save_kwargs["quality"] = 92
            self.converted_img.save(path, format=self.converted_format, **save_kwargs)
            # 下载成功：不弹窗（静默），失败时才弹窗
            self.status_var.set(f"✔ 已保存: {path}")
        except Exception as e:
            messagebox.showerror("保存失败", str(e), parent=self.root)


def main():
    if HAS_DND:
        root = TkinterDnD.Tk()
    else:
        root = tk.Tk()
    app = ImageConverterApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
