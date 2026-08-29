# -*- coding: utf-8 -*-
"""
图片压缩器（按目标大小压缩）
--------------------------------------------------
用法：
    1. 把图片拖到虚线框里（或点「选择图片」）
    2. 输入想要的大小，例如：500 KB  或  2 MB
    3. 点「开始压缩」→ 下方左右对比预览（左：原图 / 右：压缩后）
    4. 点「下载」选保存位置

规则：
    - 直接以 KB / MB 为单位指定压缩后的目标大小
    - 如果原图格式本身不支持质量压缩（BMP / ICO / GIF 等），
      会自动转成 JPG 再压缩，输出 .jpg
    - JPG / JPEG / WEBP 可直接调质量；PNG 默认无损，
      为了压到目标大小也会转成 JPG 保存
--------------------------------------------------
依赖：Pillow
可选：tkinterdnd2（支持拖拽，不装也能用「选择图片」按钮）
"""

import os
import re
import io

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from PIL import Image, ImageTk


# ============================================================
# 单一可信源：压缩结果的真实体积
# ------------------------------------------------------------
# 历史 bug：compress() 用二分法算出 quality，预览体积 out_bytes
# 只是「估计时」的体积；而 download() 却用硬编码 quality=92 重新 save，
# 导致「预览显示 783KB / 下载实际 3191KB」的严重漂移。
#
# 修正原则：全程序只在一个地方真正保存（_save_compressed），
# 产出的「真实字节数 + 字节内容」同时驱动：
#   - 右侧预览信息
#   - 底部状态栏
#   - 下载落盘（直接写入已保存的字节，不再二次 save）
# 这样三者体积在数学上完全相等，永不出错。
# ============================================================

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    HAS_DND = True
except ImportError:
    HAS_DND = False


# ============================================================
# 一、核心压缩逻辑（纯函数，方便单独测试 / 复用）
# ============================================================

# 这些格式不支持「质量参数」压缩，遇到它们就转 JPG
LOSSLESS_FORMATS = {"BMP", "ICO", "GIF", "PNG", "TIFF", "TIF"}
# 这些格式可以通过调质量来减小体积
QUALITY_FORMATS = {"JPEG", "WEBP"}


def parse_target_size(text):
    """
    把用户输入解析成字节数。
    支持：500KB、500 KB、1.5MB、0.8 MB、800（默认当 KB）
    返回 int 字节数；解析失败返回 None
    """
    text = (text or "").strip().upper().replace(" ", "")
    if not text:
        return None

    match = re.match(r"^([0-9]+(?:\.[0-9]+)?)(KB|MB|B)?$", text)
    if not match:
        return None

    number = float(match.group(1))
    unit = match.group(2) or "KB"  # 不写单位默认 KB

    if unit == "MB":
        return int(number * 1024 * 1024)
    elif unit == "KB":
        return int(number * 1024)
    else:
        return int(number)


def choose_output_format(src_format):
    """
    决定输出格式：
    - 原图是 JPG/JPEG/WEBP → 保持原格式压缩
    - 其他（PNG/BMP/GIF/ICO/TIFF 等）→ 转 JPG
    """
    if src_format in QUALITY_FORMATS:
        return src_format  # "JPEG" 或 "WEBP"
    return "JPEG"  # 默认转 JPG


def _prepare(img, fmt):
    """转 JPG 需要处理透明通道（贴白底），并把模式统一成 RGB。"""
    if fmt == "JPEG":
        if img.mode in ("RGBA", "LA", "PA") or \
           (img.mode == "P" and "transparency" in img.info):
            background = Image.new("RGB", img.size, (255, 255, 255))
            mask = img.convert("RGBA").split()[-1]
            background.paste(img.convert("RGBA"), mask=mask)
            return background
        return img.convert("RGB")
    if fmt == "WEBP":
        return img.convert("RGB")
    return img


def estimate_quality(img, fmt, target_bytes, max_try=12):
    """
    用「二分法」找合适的质量参数，使保存后体积接近 target_bytes。
    返回 (最佳质量, 实际字节数)；找不到就返回最低质量。
    """
    lo, hi = 20, 98
    best_q, best_size = 20, None

    for _ in range(max_try):
        q = (lo + hi) // 2
        buf = io.BytesIO()
        _prepare(img, fmt).save(buf, format=fmt, quality=q, optimize=True)
        size = buf.tell()

        if best_size is None or abs(size - target_bytes) < abs(best_size - target_bytes):
            best_q, best_size = q, size

        if size <= target_bytes:
            lo = q + 1   # 还能更清晰一点
        else:
            hi = q - 1   # 需要更狠的压缩

        if lo > hi:
            break

    return best_q, best_size or 0


def compress_to_size(img, target_bytes, src_format):
    """
    主入口：把 img 压到接近 target_bytes 大小。
    策略：
        1. 先调质量（不损失尺寸）
        2. 如果质量压到最低还超 → 逐步缩小尺寸再压

    返回 (压缩后PIL图, 输出格式, 最终选定的质量)。
    【注意】此处只做「参数搜索」，不负责真正落盘；
    真实体积由界面层的 _save_compressed 唯一产出，避免预览/下载不一致。
    """
    out_fmt = choose_output_format(src_format)
    current = _prepare(img, out_fmt)
    quality, _size = estimate_quality(current, out_fmt, target_bytes)

    # 质量压到最低还超标，就缩小尺寸继续压
    scale = 1.0
    while scale > 0.25:
        # 用当前 quality 真实保存一次，判断是否达标
        buf = io.BytesIO()
        save_kwargs = {"optimize": True}
        if out_fmt in QUALITY_FORMATS:
            save_kwargs["quality"] = quality
        current.save(buf, format=out_fmt, **save_kwargs)
        if buf.tell() <= target_bytes:
            break
        # 缩小后再重新估 quality
        scale *= 0.85
        new_size = (max(1, int(current.width * scale)),
                    max(1, int(current.height * scale)))
        current = _prepare(img.resize(new_size, Image.LANCZOS), out_fmt)
        quality, _size = estimate_quality(current, out_fmt, target_bytes)

    return current, out_fmt, quality


# ============================================================
# 二、界面部分
# ============================================================

# 界面配色（黑白灰，与转换器保持一致）
BG = "#ffffff"
PANEL = "#f5f5f5"
TEXT = "#222222"
DIM = "#666666"
BORDER = "#cccccc"
BTN = "#333333"
BTN_TEXT = "#ffffff"

# 预览区单张图的最大显示尺寸（宽 x 高）
PREVIEW_MAX_W = 320
PREVIEW_MAX_H = 300


def fmt_size(num_bytes):
    """把字节数转成人类可读的 KB / MB。"""
    if num_bytes is None:
        return "-"
    if num_bytes >= 1024 * 1024:
        return f"{num_bytes / (1024 * 1024):.2f} MB"
    return f"{num_bytes / 1024:.1f} KB"


class ImageCompressorApp:
    def __init__(self, root):
        self.root = root
        root.title("图片压缩器（按目标大小）")
        root.geometry("900x760")
        root.minsize(760, 680)
        root.configure(bg=BG)

        self.input_path = None
        self.src_format = None
        self.src_size_bytes = 0
        self.src_w = 0
        self.src_h = 0
        self.preview_src = None      # 原图预览 PhotoImage
        self.preview_result = None   # 压缩后预览 PhotoImage
        self.result_data = None      # 压缩结果：(PIL.Image, 格式, quality, 真实字节数, 字节内容)
        # result_data[3] = 真实体积，result_data[4] = 已保存的字节；二者一一对应，永不出错
        self._last_dir = None        # 记住上次保存的目录

        self._setup_style()
        self._build_ui()

    # ---------- 样式 ----------
    def _setup_style(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("TPanel.TFrame", background=PANEL)
        style.configure("TLabel", background=BG, foreground=TEXT,
                        font=("Microsoft YaHei", 10))
        style.configure("TButton", background=BTN, foreground=BTN_TEXT,
                        padding=(14, 6), font=("Microsoft YaHei", 10))
        style.map("TButton", background=[("active", "#555555")])
        style.configure("TEntry", fieldbackground=BG, foreground=TEXT,
                        font=("Microsoft YaHei", 11))
        try:
            from tkinter import font as tkfont
            tkfont.nametofont("TkDefaultFont").configure(family="Microsoft YaHei", size=10)
        except Exception:
            pass

    # ---------- 界面布局 ----------
    def _build_ui(self):
        # 1) 拖拽区
        self.drop_frame = tk.Label(
            self.root, text="把图片拖到这里\n支持 PNG / JPG / BMP / GIF / TIFF / WEBP / ICO",
            width=60, height=4, bg=PANEL, fg=DIM,
            relief="solid", bd=1, highlightthickness=2,
            highlightbackground=BORDER, highlightcolor=BORDER,
            font=("Microsoft YaHei", 10), justify="center",
        )
        self.drop_frame.pack(fill="x", padx=20, pady=(15, 10))
        if HAS_DND:
            self.drop_frame.drop_target_register(DND_FILES)
            self.drop_frame.dnd_bind("<<Drop>>", self.on_drop)

        # 2) 文件信息
        self.info_var = tk.StringVar(value="未选择图片")
        ttk.Label(self.root, textvariable=self.info_var, anchor="w",
                  background=BG, foreground=TEXT).pack(fill="x", padx=20)

        # 3) 目标大小输入
        size_row = ttk.Frame(self.root)
        size_row.pack(fill="x", padx=20, pady=8)
        ttk.Label(size_row, text="压缩到：").pack(side="left")
        self.size_entry = ttk.Entry(size_row, width=14, justify="center")
        self.size_entry.insert(0, "500 KB")
        self.size_entry.pack(side="left", padx=(6, 6))
        ttk.Label(size_row, text="（支持 KB / MB，如 200KB、1.5MB）",
                  foreground=DIM).pack(side="left")

        # 4) 操作按钮
        btn_row = ttk.Frame(self.root)
        btn_row.pack(fill="x", padx=20, pady=(4, 8))
        ttk.Button(btn_row, text="选择图片…", command=self.choose_file).pack(side="left", padx=(0, 10))
        ttk.Button(btn_row, text="开始压缩", command=self.compress).pack(side="left")

        # 5) 左右对比预览区
        self._build_preview()

        # 6) 底部：状态 + 下载按钮（贴底）
        bottom = ttk.Frame(self.root)
        bottom.pack(side="bottom", fill="x", padx=20, pady=(8, 12))

        self.status_var = tk.StringVar(value="")
        ttk.Label(bottom, textvariable=self.status_var, foreground=DIM,
                  anchor="w").pack(side="left", fill="x", expand=True)

        self.download_btn = ttk.Button(bottom, text="下载压缩后的图片",
                                       command=self.download, state="disabled")
        self.download_btn.pack(side="right")

    def _build_preview(self):
        """预览区：左 原图 / 右 压缩后，中间用箭头分隔，底部各标信息。"""
        container = ttk.Frame(self.root)
        container.pack(padx=20, pady=(4, 8), fill="both", expand=True)

        # 左右两栏，等宽
        left = ttk.Frame(container)
        right = ttk.Frame(container)
        left.pack(side="left", fill="both", expand=True)
        right.pack(side="left", fill="both", expand=True, padx=(10, 0))

        # ---- 左：原图 ----
        self.src_label = tk.Label(
            left, text="原图", bg=PANEL, fg=TEXT,
            font=("Microsoft YaHei", 10, "bold"), anchor="center",
        )
        self.src_label.pack(fill="x", pady=(0, 4))

        self.src_image_label = tk.Label(
            left, text="预览区域", bg=PANEL, fg=DIM,
            width=38, height=13, relief="solid", bd=1,
            font=("Microsoft YaHei", 10),
        )
        self.src_image_label.pack(fill="both", expand=True)

        self.src_info_var = tk.StringVar(value="")
        ttk.Label(left, textvariable=self.src_info_var, anchor="center",
                  foreground=DIM).pack(fill="x", pady=(4, 0))

        # ---- 右：压缩后 ----
        self.result_label = tk.Label(
            right, text="压缩后", bg=PANEL, fg=TEXT,
            font=("Microsoft YaHei", 10, "bold"), anchor="center",
        )
        self.result_label.pack(fill="x", pady=(0, 4))

        self.result_image_label = tk.Label(
            right, text="压缩后预览\n（点击「开始压缩」后显示）", bg=PANEL, fg=DIM,
            width=38, height=13, relief="solid", bd=1,
            font=("Microsoft YaHei", 10), justify="center",
        )
        self.result_image_label.pack(fill="both", expand=True)

        self.result_info_var = tk.StringVar(value="")
        ttk.Label(right, textvariable=self.result_info_var, anchor="center",
                  foreground=DIM).pack(fill="x", pady=(4, 0))

    # ---------- 拖拽 / 选择 ----------
    def on_drop(self, event):
        raw = event.data
        if "{" in raw:
            import re as _re
            matches = _re.findall(r"\{([^{}]+)\}", raw)
            candidates = matches if matches else [raw]
        else:
            candidates = raw.split()
        for cand in candidates:
            path = cand.strip()
            if path and os.path.isfile(path):
                self.load_image(path)
                return

    def choose_file(self):
        path = filedialog.askopenfilename(
            title="选择要压缩的图片",
            filetypes=[("图片", "*.png *.jpg *.jpeg *.bmp *.gif *.tiff *.tif *.webp *.ico"),
                       ("所有文件", "*.*")],
        )
        if path:
            self.load_image(path)

    def load_image(self, path):
        try:
            img = Image.open(path)
            img.verify()
            img = Image.open(path)  # verify 后需重新打开
        except Exception as e:
            messagebox.showerror("错误", f"无法识别图片：\n{e}")
            return

        ext_fmt = {
            ".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".bmp": "BMP",
            ".gif": "GIF", ".tif": "TIFF", ".tiff": "TIFF",
            ".webp": "WEBP", ".ico": "ICO",
        }
        self.src_format = ext_fmt.get(os.path.splitext(path)[1].lower(), img.format or "UNKNOWN")
        self.input_path = path
        self.src_size_bytes = os.path.getsize(path)
        self.src_w, self.src_h = img.width, img.height

        self.info_var.set(
            f"已加载：{os.path.basename(path)}  ｜  {self.src_format}, "
            f"{self.src_w}×{self.src_h}  ｜  原大小 {fmt_size(self.src_size_bytes)}"
        )

        # 加载时只显示原图预览
        self._show_src_preview(img)
        self._clear_result_preview()

        self.status_var.set("")
        self.download_btn.configure(state="disabled")
        self.result_data = None

    @staticmethod
    def _thumb(img, max_w, max_h):
        """把 PIL 图等比缩到 (max_w, max_h) 以内，返回 RGBA 缩略图。"""
        w, h = img.size
        if w <= 0 or h <= 0:
            w, h = 1, 1
        ratio = min(max_w / w, max_h / h, 1)
        thumb_size = (max(1, int(w * ratio)), max(1, int(h * ratio)))
        thumb = img.copy().convert("RGBA")
        thumb.thumbnail(thumb_size, Image.LANCZOS)
        return thumb

    def _show_src_preview(self, img):
        """显示左侧原图预览。"""
        thumb = self._thumb(img, PREVIEW_MAX_W, PREVIEW_MAX_H)
        try:
            self.preview_src = ImageTk.PhotoImage(thumb)
        except Exception as e:
            self.status_var.set(f"预览生成失败：{e}")
            return

        self.src_image_label.configure(text="", image=self.preview_src)
        self.src_image_label.image = self.preview_src  # 防止被 GC 回收
        self.src_info_var.set(f"{self.src_w}×{self.src_h}  ｜  {fmt_size(self.src_size_bytes)}")

    def _clear_result_preview(self):
        """清空右侧压缩后预览，回到初始提示。"""
        self.result_image_label.configure(
            text="压缩后预览\n（点击「开始压缩」后显示）", image=""
        )
        self.result_image_label.image = None
        self.preview_result = None
        self.result_info_var.set("")

    def _show_result_preview(self, img, out_fmt, out_bytes):
        """显示右侧压缩后预览，并更新底部信息。"""
        thumb = self._thumb(img, PREVIEW_MAX_W, PREVIEW_MAX_H)
        try:
            self.preview_result = ImageTk.PhotoImage(thumb)
        except Exception as e:
            self.status_var.set(f"预览生成失败：{e}")
            return

        self.result_image_label.configure(text="", image=self.preview_result)
        self.result_image_label.image = self.preview_result

        ext = "jpg" if out_fmt == "JPEG" else out_fmt.lower()
        self.result_info_var.set(f"{img.width}×{img.height}  ｜  {fmt_size(out_bytes)}  (.{ext})")

    # ---------- 压缩 ----------
    def _save_compressed(self, img, fmt, quality):
        """
        唯一真正落盘出口：把 (img, fmt, quality) 保存成字节。
        返回 (真实字节数, 字节内容 bytes)。

        预览 / 状态栏 / 下载 三者共用这份「真实字节数 + 字节内容」，
        保证「预览显示的体积 ≡ 下载文件的实际体积」，绝不二次猜测。
        """
        buf = io.BytesIO()
        kwargs = {"optimize": True}
        if fmt in QUALITY_FORMATS:
            kwargs["quality"] = quality
        img.save(buf, format=fmt, **kwargs)
        blob = buf.getvalue()
        return len(blob), blob

    def compress(self):
        if not self.input_path:
            messagebox.showwarning("提示", "请先选择一张图片。")
            return

        target = parse_target_size(self.size_entry.get())
        if not target or target <= 0:
            messagebox.showerror("参数错误", "请输入合法的大小，例如：\n200KB、500 KB、1.5MB")
            return

        if target >= self.src_size_bytes:
            # 目标比原图还大，没必要压，直接提示
            messagebox.showinfo(
                "无需压缩",
                f"目标大小 {fmt_size(target)} 大于等于原图 {fmt_size(self.src_size_bytes)}，\n"
                f"已直接采用原图质量。",
            )

        self.status_var.set("压缩中…")
        self.root.update_idletasks()

        try:
            img = Image.open(self.input_path)
            result_img, out_fmt, quality = compress_to_size(
                img, target, self.src_format
            )
        except Exception as e:
            # 压缩失败保留原图预览，避免"点了什么都没有"
            try:
                self._show_src_preview(Image.open(self.input_path))
            except Exception:
                pass
            messagebox.showerror("压缩失败", str(e))
            return

        # ★ 唯一落盘：用最终选定的 (out_fmt, quality) 真正保存一次，
        #   拿到「真实字节数 + 字节内容」，预览/状态栏/下载全部基于此，
        #   杜绝「预览体积 ≠ 下载体积」的旧 bug。
        try:
            real_bytes, blob = self._save_compressed(result_img, out_fmt, quality)
        except Exception as e:
            messagebox.showerror("压缩失败", f"生成压缩数据时出错：\n{e}")
            return

        # 保存结果到内存，供预览 / 下载使用（体积是真实值）
        self.result_data = (result_img, out_fmt, quality, real_bytes, blob)

        # 左侧原图 + 右侧压缩后，左右对比
        try:
            src_img = Image.open(self.input_path)
        except Exception:
            src_img = result_img
        self._show_src_preview(src_img)
        self._show_result_preview(result_img, out_fmt, real_bytes)

        # 输出格式后缀
        ext = "jpg" if out_fmt == "JPEG" else out_fmt.lower()

        # 判断是否发生了格式转换
        converted = ""
        if out_fmt == "JPEG" and self.src_format not in QUALITY_FORMATS:
            converted = f"（原格式 {self.src_format} 不支持压缩，已自动转 JPG）"

        self.status_var.set(
            f"压缩完成：{fmt_size(self.src_size_bytes)} → {fmt_size(real_bytes)}  "
            f"｜  输出 .{ext}  质量 {quality}{converted}"
        )
        self.download_btn.configure(state="normal")

    # ---------- 下载 ----------
    def download(self):
        if not self.result_data:
            return
        result_img, out_fmt, quality, real_bytes, blob = self.result_data
        ext = "jpg" if out_fmt == "JPEG" else out_fmt.lower()
        default_name = os.path.splitext(os.path.basename(self.input_path))[0] \
                       + f"_compressed.{ext}"

        path = filedialog.asksaveasfilename(
            title="保存压缩后的图片",
            initialfile=default_name,
            defaultextension=f".{ext}",
            filetypes=[(f"{out_fmt} 文件", f"*.{ext}"), ("所有文件", "*.*")],
            initialdir=self._last_dir,
        )
        if not path:
            return

        try:
            # ★ 直接写入压缩时已保存的字节内容（blob），
            #   不重新 save、不使用别的 quality，因此：
            #   下载文件体积 ≡ 预览/状态栏显示的 real_bytes，100% 一致。
            with open(path, "wb") as f:
                f.write(blob)
            self._last_dir = os.path.dirname(path)
            # 成功：不打扰，只更新状态栏（用 real_bytes，即预览时的同一数字）
            self.status_var.set(f"✔ 已保存：{path}  （{fmt_size(real_bytes)}）")
        except Exception as e:
            # 失败：才弹窗
            messagebox.showerror("保存失败", str(e))


def main():
    root = TkinterDnD.Tk() if HAS_DND else tk.Tk()
    ImageCompressorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
