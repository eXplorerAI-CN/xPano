# -*- coding: utf-8 -*-
import os
import re
import shutil
import subprocess
import json
import piexif
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor
from tqdm import tqdm
import threading

# --- 配置区 ---
SUPPORTED_EXTENSIONS = ['.insv', '.osv', '.mp4']
MAX_WORKERS = 4  # 建议根据显存/内存调整，不宜过多，否则进度条会刷屏
GPU_ACCEL = False

def get_video_info(file_path):
    try:
        cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type", "-of", "json", str(file_path)]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        data = json.loads(result.stdout)
        duration = float(data['format']['duration'])
        video_streams = [s for s in data['streams'] if s['codec_type'] == 'video']
        return duration, len(video_streams)
    except: return 0.0, 0

def apply_exif(img_path, model, make):
    try:
        exif_dict = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "thumbnail": None}
        exif_dict['0th'][piexif.ImageIFD.Make] = make.encode()
        exif_dict['0th'][piexif.ImageIFD.Model] = model.encode()
        piexif.insert(piexif.dump(exif_dict), str(img_path))
    except: pass

def run_ffmpeg_with_progress(cmd, total_frames, task_name, position):
    """带实时速度监控的FFmpeg执行器"""
    pbar = tqdm(total=total_frames, desc=f" {task_name[:15]}...", 
                unit="f", position=position, leave=False, 
                bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]')
    
    process = subprocess.Popen(cmd, stderr=subprocess.PIPE, universal_newlines=True, encoding='utf-8')
    
    frame_pattern = re.compile(r"frame=\s*(\d+)")
    last_frame = 0
    
    for line in process.stderr:
        match = frame_pattern.search(line)
        if match:
            curr_frame = int(match.group(1))
            pbar.update(curr_frame - last_frame)
            last_frame = curr_frame
            
    process.wait()
    pbar.close()

def process_single_task(task, fps, base_dir, position):
    c_name = task['clean_name']
    out_root = base_dir / c_name
    out_root.mkdir(exist_ok=True)
    tmp_l, tmp_r = base_dir/f"tmp_{c_name}_L", base_dir/f"tmp_{c_name}_R"
    tmp_l.mkdir(exist_ok=True); tmp_r.mkdir(exist_ok=True)

    # 计算总帧数用于进度条
    dur, streams = get_video_info(task['left_file'])
    total_expected = int(dur * fps)

    cmd = ["ffmpeg", "-hide_banner", "-progress", "pipe:2", "-i", str(task['left_file'])]
    if GPU_ACCEL: cmd.insert(2, "-hwaccel"); cmd.insert(3, "cuda")

    if task['type'] == 'insta_split':
        cmd += ["-i", str(task['right_file']), 
                "-map", "0:0", "-vf", f"fps={fps}", "-q:v", "2", f"{tmp_l}/f_%05d.jpg",
                "-map", "1:0", "-vf", f"fps={fps}", "-q:v", "2", f"{tmp_r}/f_%05d.jpg"]
    else:
        cmd += ["-map", "0:0", "-vf", f"fps={fps}", "-q:v", "2", f"{tmp_l}/f_%05d.jpg",
                "-map", "0:1", "-vf", f"fps={fps}", "-q:v", "2", f"{tmp_r}/f_%05d.jpg"]

    # 执行并监控
    run_ffmpeg_with_progress(cmd, total_expected, c_name, position)

    # 后处理：归档与EXIF (这部分跑得极快)
    make = "Insta360" if task['left_file'].suffix.lower() == ".insv" else "DJI"
    for li in sorted(tmp_l.glob("*.jpg")):
        f_num = re.search(r'f_(\d+)', li.name).group(1)
        f_folder = out_root / f"{c_name}_frame_{f_num}"
        f_folder.mkdir(exist_ok=True)
        
        target_l = f_folder / f"{c_name}_frame_{f_num}_left.jpg"
        shutil.move(str(li), str(target_l))
        apply_exif(target_l, f"{make.lower()}_left", make)
        
        ri = tmp_r / f"f_{f_num}.jpg"
        if ri.exists():
            target_r = f_folder / f"{c_name}_frame_{f_num}_right.jpg"
            shutil.move(str(ri), str(target_r))
            apply_exif(target_r, f"{make.lower()}_right", make)

    shutil.rmtree(tmp_l); shutil.rmtree(tmp_r)
    return True

def main():
    base_dir = Path(__file__).parent.resolve()
    print(f"\n[监控开启] 并发任务数: {MAX_WORKERS} | 实时汇报抽帧速率")
    
    # 任务扫描逻辑 (保持不变)
    all_files = [f for f in base_dir.iterdir() if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS]
    tasks, processed_files = [], set()
    insta_re = re.compile(r'(VID_\d+_\d+)_(00|10)_(\d+)')
    for f in all_files:
        if f in processed_files: continue
        match = insta_re.search(f.name)
        if match:
            prefix, side, suffix = match.groups()
            clean_name, other_side = f"{prefix}_00_{suffix}", ("10" if side == "00" else "00")
            partner = base_dir / f"{prefix}_{other_side}_{suffix}.insv"
            if partner.exists():
                tasks.append({'clean_name': clean_name, 'left_file': (f if side == "00" else partner), 'right_file': (f if side == "10" else partner), 'type': 'insta_split'})
                processed_files.update([f, partner]); continue
    for f in all_files:
        if f in processed_files: continue
        _, st = get_video_info(f)
        tasks.append({'clean_name': f.stem, 'left_file': f, 'right_file': f, 'type': 'dji_dual' if st >= 2 else 'single'})
        processed_files.add(f)

    if not tasks: return

    fps = float(input("请输入抽帧率 (默认 1): ") or 1.0)
    
    # 总进度条固定在最下方
    print("\n" * (MAX_WORKERS + 1)) # 为多条进度条预留空间
    main_pbar = tqdm(total=len(tasks), desc="[总任务进度]", position=0, unit="file")

    with ProcessPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = []
        for i, t in enumerate(tasks):
            # position 参数决定了进度条在屏幕的第几行
            pos = (i % MAX_WORKERS) + 1
            futures.append(executor.submit(process_single_task, t, fps, base_dir, pos))
        
        for future in futures:
            if future.result():
                main_pbar.update(1)

    main_pbar.close()
    print("\n" * MAX_WORKERS + "处理完成！")

if __name__ == "__main__":
    main()