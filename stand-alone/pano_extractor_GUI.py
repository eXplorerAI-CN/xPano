# -*- coding: utf-8 -*-
import os
import re
import sys
import shutil
import subprocess
import json
import queue
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText

# 尝试导入拖拽库，无法导入时平滑降级
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    HAS_DND = True
except ImportError:
    HAS_DND = False

# --- 全局配置 ---
SUPPORTED_EXTENSIONS = ['.insv', '.osv', '.mp4']

def get_video_info(file_path):
    try:
        cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type", "-of", "json", str(file_path)]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        data = json.loads(result.stdout)
        duration = float(data['format']['duration'])
        video_streams = [s for s in data['streams'] if s['codec_type'] == 'video']
        return duration, len(video_streams)
    except:
        return 0.0, 0

def apply_exif(img_path, model, make):
    try:
        import piexif
        exif_dict = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "thumbnail": None}
        exif_dict['0th'][piexif.ImageIFD.Make] = make.encode('utf-8')
        exif_dict['0th'][piexif.ImageIFD.Model] = model.encode('utf-8')
        piexif.insert(piexif.dump(exif_dict), str(img_path))
    except Exception as e:
        pass

def escape_filter_path(path_obj):
    """转换并转义 FFmpeg 滤镜中的路径（兼容Windows路径下的冒号与单引号）"""
    p_str = path_obj.as_posix()
    p_str = p_str.replace(':', '\\:')  # 关键：转义Windows盘符冒号
    p_str = p_str.replace("'", "'\\\\''")  # 转义单引号
    return p_str

def process_single_task(task, fps, lut_path, gpu_accel, log_queue):
    c_name = task['clean_name']
    out_root = task['output_dir']
    out_root.mkdir(exist_ok=True)
    
    # 临时目录存放在视频所在的分区下，提升IO性能
    tmp_l = task['base_dir'] / f"tmp_{c_name}_L"
    tmp_r = task['base_dir'] / f"tmp_{c_name}_R"
    tmp_l.mkdir(exist_ok=True)
    if task['type'] != 'single':
        tmp_r.mkdir(exist_ok=True)

    log_queue.put(('log', f"▶ [正在提取] 任务: {c_name} | 类型: {task['type']}"))

    # 构建 FFmpeg 命令
    cmd = ["ffmpeg", "-hide_banner", "-y"]
    
    # 视频输入
    if gpu_accel:
        cmd += ["-hwaccel", "cuda"]
    cmd += ["-i", str(task['left_file'])]
    
    if task['type'] == 'insta_split':
        if gpu_accel:
            cmd += ["-hwaccel", "cuda"]
        cmd += ["-i", str(task['right_file'])]

    # 构建滤镜链 (整合抽帧与 3D LUT)
    filter_graph = f"fps={fps}"
    if lut_path:
        escaped_lut = escape_filter_path(Path(lut_path))
        filter_graph += f",lut3d=file='{escaped_lut}'"  # 核心：一步到位完成色彩空间转换

    # 输出映射
    if task['type'] == 'insta_split':
        cmd += [
            "-map", "0:0", "-vf", filter_graph, "-q:v", "2", f"{tmp_l}/f_%05d.jpg",
            "-map", "1:0", "-vf", filter_graph, "-q:v", "2", f"{tmp_r}/f_%05d.jpg"
        ]
    elif task['type'] == 'dji_dual':
        cmd += [
            "-map", "0:0", "-vf", filter_graph, "-q:v", "2", f"{tmp_l}/f_%05d.jpg",
            "-map", "0:1", "-vf", filter_graph, "-q:v", "2", f"{tmp_r}/f_%05d.jpg"
        ]
    else: # single 单镜头普通提取
        cmd += [
            "-map", "0:0", "-vf", filter_graph, "-q:v", "2", f"{tmp_l}/f_%05d.jpg"
        ]

    # 执行抽帧
    try:
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    except subprocess.CalledProcessError as e:
        log_queue.put(('log', f"❌ [失败] {c_name} 抽帧出错:\n{e.stderr}"))
        shutil.rmtree(tmp_l, ignore_errors=True)
        shutil.rmtree(tmp_r, ignore_errors=True)
        return False

    # 后处理：归档并打入 EXIF 元数据
    make = "Insta360" if task['left_file'].suffix.lower() == ".insv" else "DJI"
    
    left_images = sorted(tmp_l.glob("*.jpg"))
    for li in left_images:
        f_num = re.search(r'f_(\d+)', li.name).group(1)
        f_folder = out_root / f"{c_name}_frame_{f_num}"
        f_folder.mkdir(exist_ok=True)
        
        target_l = f_folder / f"{c_name}_frame_{f_num}_left.jpg"
        shutil.move(str(li), str(target_l))
        apply_exif(target_l, f"{make.lower()}_left", make)
        
        if task['type'] != 'single':
            ri = tmp_r / f"f_{f_num}.jpg"
            if ri.exists():
                target_r = f_folder / f"{c_name}_frame_{f_num}_right.jpg"
                shutil.move(str(ri), str(target_r))
                apply_exif(target_r, f"{make.lower()}_right", make)

    # 清理临时目录
    shutil.rmtree(tmp_l, ignore_errors=True)
    if task['type'] != 'single':
        shutil.rmtree(tmp_r, ignore_errors=True)
        
    log_queue.put(('log', f"✔ [完成] {c_name} 处理完毕，共生成了 {len(left_images)} 帧。"))
    return True


class PanoExtractorGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Osmo & Insta360 全景抽帧调色工具 (带LUT支持)")
        self.root.geometry("800x650")
        
        self.selected_files = set()
        self.queue = queue.Queue()
        self.running = False
        self.executor = None
        
        self.setup_ui()
        self.setup_dnd()
        self.check_queue()

    def setup_ui(self):
        # 1. 文件导入区
        frame_files = ttk.LabelFrame(self.root, text=" 1. 待处理全景视频/包含视频的文件夹 (支持拖拽导入) ", padding=10)
        frame_files.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        self.listbox = tk.Listbox(frame_files, selectmode=tk.MULTIPLE, height=8)
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        scrollbar = ttk.Scrollbar(frame_files, orient="vertical", command=self.listbox.yview)
        scrollbar.pack(side=tk.LEFT, fill=tk.Y)
        self.listbox.config(yscrollcommand=scrollbar.set)
        
        btn_frame = ttk.Frame(frame_files)
        btn_frame.pack(side=tk.RIGHT, fill=tk.Y, padx=5)
        
        ttk.Button(btn_frame, text="添加视频文件", command=self.add_files_dialog).pack(fill=tk.X, pady=2)
        ttk.Button(btn_frame, text="添加文件夹", command=self.add_dir_dialog).pack(fill=tk.X, pady=2)
        ttk.Button(btn_frame, text="移除选中", command=self.remove_selected).pack(fill=tk.X, pady=2)
        ttk.Button(btn_frame, text="清空列表", command=self.clear_list).pack(fill=tk.X, pady=2)

        # 2. 参数设置区
        frame_settings = ttk.LabelFrame(self.root, text=" 2. 配置参数 (支持拖拽.cube文件导入) ", padding=10)
        frame_settings.pack(fill=tk.X, padx=10, pady=5)
        
        # LUT 文件
        ttk.Label(frame_settings, text="3D LUT 文件 (.cube):").grid(row=0, column=0, sticky=tk.W, pady=5)
        self.lut_var = tk.StringVar()
        self.lut_entry = ttk.Entry(frame_settings, textvariable=self.lut_var)
        self.lut_entry.grid(row=0, column=1, sticky=tk.EW, padx=5, pady=5)
        frame_settings.columnconfigure(1, weight=1)
        ttk.Button(frame_settings, text="浏览...", command=self.browse_lut).grid(row=0, column=2, pady=5)
        
        # 抽帧率等其他参数
        params_subframe = ttk.Frame(frame_settings)
        params_subframe.grid(row=1, column=0, columnspan=3, sticky=tk.W, pady=5)
        
        ttk.Label(params_subframe, text="抽帧率 (FPS):").pack(side=tk.LEFT, padx=5)
        self.fps_var = tk.StringVar(value="1.0")
        ttk.Entry(params_subframe, textvariable=self.fps_var, width=8).pack(side=tk.LEFT, padx=5)
        
        ttk.Label(params_subframe, text="并发任务数:").pack(side=tk.LEFT, padx=15)
        self.workers_var = tk.StringVar(value="4")
        ttk.Entry(params_subframe, textvariable=self.workers_var, width=5).pack(side=tk.LEFT, padx=5)
        
        self.gpu_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(params_subframe, text="开启 CUDA 硬件加速", variable=self.gpu_var).pack(side=tk.LEFT, padx=15)

        # 3. 运行控制与进度
        frame_control = ttk.Frame(self.root, padding=10)
        frame_control.pack(fill=tk.X, padx=10)
        
        self.btn_start = ttk.Button(frame_control, text=" 🚀 开始执行 ", width=15, command=self.start_processing)
        self.btn_start.pack(side=tk.LEFT, padx=5)
        
        self.btn_stop = ttk.Button(frame_control, text=" 🛑 停止 ", width=10, command=self.stop_processing, state=tk.DISABLED)
        self.btn_stop.pack(side=tk.LEFT, padx=5)
        
        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(frame_control, variable=self.progress_var, maximum=100)
        self.progress_bar.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=15)

        # 4. 实时日志区
        frame_log = ttk.LabelFrame(self.root, text=" 3. 任务日志监控 ", padding=5)
        frame_log.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        self.log_text = ScrolledText(frame_log, height=10, bg="#2b2b2b", fg="#a9b7c6", font=("Consolas", 9))
        self.log_text.pack(fill=tk.BOTH, expand=True)

    def setup_dnd(self):
        """设置拖放"""
        if HAS_DND:
            self.listbox.drop_target_register(DND_FILES)
            self.listbox.dnd_bind('<<Drop>>', self.handle_listbox_drop)
            
            self.lut_entry.drop_target_register(DND_FILES)
            self.lut_entry.dnd_bind('<<Drop>>', self.handle_lut_drop)
            self.log_text.insert(tk.END, "[系统] 拖拽引擎已成功载入，您可以将视频、文件夹或 .cube 文件直接拖入界面！\n\n")
        else:
            self.log_text.insert(tk.END, "[系统] 未检测到 tkinterdnd2 依赖。拖放功能已被禁用，请使用常规按钮导入文件。\n"
                                         "如需开启拖放，可在终端运行: pip install tkinterdnd2\n\n")

    # --- 拖放与文件导入逻辑 ---
    def parse_dnd_paths(self, raw_data):
        """解析拖拽事件路径（兼容空格与大括号）"""
        paths = []
        # 正则处理大括号 `{path}` 或无空格路径 `path`
        pattern = re.compile(r'\{([^}]+)\}|(\S+)')
        for match in pattern.finditer(raw_data):
            path_str = match.group(1) or match.group(2)
            if path_str:
                paths.append(Path(path_str))
        return paths

    def handle_listbox_drop(self, event):
        paths = self.parse_dnd_paths(event.data)
        self.add_paths(paths)

    def handle_lut_drop(self, event):
        paths = self.parse_dnd_paths(event.data)
        if paths:
            lut_file = paths[0]
            if lut_file.suffix.lower() == '.cube':
                self.lut_var.set(str(lut_file))
                self.log_text.insert(tk.END, f"[系统] 已成功拖入并应用调色预设: {lut_file.name}\n")
            else:
                messagebox.showerror("文件类型错误", "调色预设必须为 .cube 格式！")

    def add_paths(self, paths):
        for p in paths:
            if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS:
                self.selected_files.add(p)
            elif p.is_dir():
                for sub_file in p.rglob("*"):
                    if sub_file.is_file() and sub_file.suffix.lower() in SUPPORTED_EXTENSIONS:
                        self.selected_files.add(sub_file)
        self.update_listbox()

    def add_files_dialog(self):
        ext_filter = " ".join([f"*{ext}" for ext in SUPPORTED_EXTENSIONS])
        files = filedialog.askopenfilenames(title="选择全景视频文件", filetypes=[("全景视频文件", ext_filter)])
        if files:
            self.add_paths([Path(f) for f in files])

    def add_dir_dialog(self):
        dir_path = filedialog.askdirectory(title="选择包含视频文件的文件夹")
        if dir_path:
            self.add_paths([Path(dir_path)])

    def remove_selected(self):
        selected_indices = list(self.listbox.curselection())
        selected_indices.reverse()  # 倒序删除避免索引错位
        for idx in selected_indices:
            file_str = self.listbox.get(idx)
            self.selected_files.discard(Path(file_str))
        self.update_listbox()

    def clear_list(self):
        self.selected_files.clear()
        self.update_listbox()

    def update_listbox(self):
        self.listbox.delete(0, tk.END)
        for f in sorted(list(self.selected_files)):
            self.listbox.insert(tk.END, str(f))

    def browse_lut(self):
        file_path = filedialog.askopenfilename(title="选择 3D LUT 文件", filetypes=[("3D LUT 文件", "*.cube")])
        if file_path:
            self.lut_var.set(file_path)

    # --- 核心业务扫描与调度逻辑 ---
    def scan_tasks(self):
        """扫描所导入的文件并进行合理的双镜头封装/Insta分镜头对齐组装"""
        tasks = []
        processed_files = set()
        
        # 按照文件夹对导入的文件分组，避免跨文件夹的相同命名误配对
        files_by_dir = {}
        for f in self.selected_files:
            files_by_dir.setdefault(f.parent, []).append(f)
            
        insta_re = re.compile(r'(VID_\d+_\d+)_(00|10)_(\d+)')
        
        for parent, files in files_by_dir.items():
            # 先寻找并配对 Insta360 .insv 分组
            for f in files:
                if f in processed_files: continue
                match = insta_re.search(f.name)
                if match:
                    prefix, side, suffix = match.groups()
                    clean_name = f"{prefix}_00_{suffix}"
                    other_side = "10" if side == "00" else "00"
                    partner = parent / f"{prefix}_{other_side}_{suffix}.insv"
                    # 如果配对伙伴存在于同目录中（即使导入列表中用户只选了一个，也会自动识别）
                    if partner.exists():
                        tasks.append({
                            'clean_name': clean_name,
                            'left_file': f if side == "00" else partner,
                            'right_file': f if side == "10" else partner,
                            'type': 'insta_split',
                            'output_dir': parent / clean_name,
                            'base_dir': parent
                        })
                        processed_files.update([f, partner])
                        continue
                        
            # 处理其他单镜头/大疆 DJI 双镜头内封装模式
            for f in files:
                if f in processed_files: continue
                _, st = get_video_info(f)
                tasks.append({
                    'clean_name': f.stem,
                    'left_file': f,
                    'right_file': f,
                    'type': 'dji_dual' if st >= 2 else 'single',
                    'output_dir': f.parent / f.stem,
                    'base_dir': f.parent
                })
                processed_files.add(f)
                
        return tasks

    def start_processing(self):
        if not self.selected_files:
            messagebox.showwarning("警告", "待处理列表为空！请先导入视频文件。")
            return
            
        # 参数校验
        try:
            fps = float(self.fps_var.get())
            if fps <= 0: raise ValueError
        except ValueError:
            messagebox.showerror("参数错误", "抽帧率必须为大于 0 的浮点数或整数！")
            return

        try:
            max_workers = int(self.workers_var.get())
            if max_workers <= 0: raise ValueError
        except ValueError:
            messagebox.showerror("参数错误", "并发数必须为正整数！")
            return

        lut_file = self.lut_var.get().strip()
        if lut_file and not Path(lut_file).exists():
            messagebox.showerror("文件不存在", f"无法找到指定的 LUT 文件: \n{lut_file}")
            return

        tasks = self.scan_tasks()
        if not tasks:
            messagebox.showwarning("警告", "没有扫描到可供抽帧的全景任务。")
            return

        # UI 状态切换
        self.running = True
        self.btn_start.config(state=tk.DISABLED)
        self.btn_stop.config(state=tk.NORMAL)
        self.set_ui_state(tk.DISABLED)
        
        self.log_text.delete(1.0, tk.END)
        self.log_text.insert(tk.END, f"=== 开启抽帧处理 ===\n")
        self.log_text.insert(tk.END, f"➤ 总分配任务数: {len(tasks)}\n")
        self.log_text.insert(tk.END, f"➤ 抽帧帧率: {fps} FPS\n")
        self.log_text.insert(tk.END, f"➤ 应用调色LUT: {Path(lut_file).name if lut_file else '不使用'}\n")
        self.log_text.insert(tk.END, f"----------------------------------------\n")
        
        # 启动后台处理线程，避免阻塞 Tkinter 主循环
        self.progress_bar['maximum'] = len(tasks)
        self.progress_var.set(0)
        
        self.executor_thread = threading.Thread(
            target=self.run_tasks_thread, 
            args=(tasks, fps, lut_file, max_workers, self.gpu_var.get()), 
            daemon=True
        )
        self.executor_thread.start()

    def run_tasks_thread(self, tasks, fps, lut_file, max_workers, gpu_accel):
        # 使用 ThreadPoolExecutor（FFmpeg 进程释放了 GIL，多线程比多进程在 GUI 中能更好地防止通讯死锁）
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        futures = []
        completed = 0
        
        for t in tasks:
            fut = self.executor.submit(process_single_task, t, fps, lut_file, gpu_accel, self.queue)
            futures.append((fut, t['clean_name']))

        for fut, name in futures:
            try:
                success = fut.result()
                if success:
                    completed += 1
            except Exception as e:
                self.queue.put(('log', f"❌ [任务异常] {name} 遇到未捕获错误: {e}"))
            
            # 每完成一个任务，主线程更新进度条
            self.queue.put(('progress', completed))
            
        self.queue.put(('finished', f"处理全部完成！成功完成数: {completed}/{len(tasks)}"))

    def stop_processing(self):
        if not self.running: return
        if messagebox.askyesno("中止任务", "是否确定终止所有未完成的抽帧任务？"):
            self.running = False
            if self.executor:
                self.executor.shutdown(wait=False, cancel_futures=True)
            self.log_text.insert(tk.END, "\n🛑 用户手动中止了处理任务。\n")
            self.reset_ui_state()

    def set_ui_state(self, state):
        self.listbox.config(state=state)
        self.lut_entry.config(state=state)

    def reset_ui_state(self):
        self.running = False
        self.btn_start.config(state=tk.NORMAL)
        self.btn_stop.config(state=tk.DISABLED)
        self.set_ui_state(tk.NORMAL)

    def check_queue(self):
        """主线程轮询安全更新GUI"""
        try:
            while True:
                msg_type, data = self.queue.get_nowait()
                if msg_type == 'log':
                    self.log_text.insert(tk.END, data + "\n")
                    self.log_text.see(tk.END)
                elif msg_type == 'progress':
                    self.progress_var.set(data)
                elif msg_type == 'finished':
                    self.log_text.insert(tk.END, f"\n{data}\n")
                    self.log_text.see(tk.END)
                    messagebox.showinfo("处理完成", data)
                    self.reset_ui_state()
                self.queue.task_done()
        except queue.Empty:
            pass
        self.root.after(100, self.check_queue)


def main():
    # 强制在 Windows 上将控制台编码设置为 utf-8
    if sys.platform.startswith('win'):
        import os
        os.environ["PYTHONIOENCODING"] = "utf-8"
        
    if HAS_DND:
        root = TkinterDnD.Tk()
    else:
        root = tk.Tk()
        
    app = PanoExtractorGUI(root)
    root.mainloop()

if __name__ == "__main__":
    main()