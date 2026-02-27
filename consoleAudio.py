# -*- coding: utf-8 -*-
import numpy as np
import pyaudio
import os
import threading
import time
from collections import deque
import sys
import librosa  # 需要安装：pip install librosa

# 配置参数
RATE = 16000
CHUNK = 512
NUM_BINS = 60          # 保持60个bin
BIN_WIDTH = 2
HISTORY_LINES = 20
SILENCE_DB_THRESHOLD = 55
FPS = 30
STATS_WINDOW_SEC = 3
BITS=8 # max FF

# 全局变量
audio_buffer = deque(maxlen=100)
playback_queue = deque(maxlen=10)
stop_flag = False
history_lines = deque(maxlen=20)
db_history = deque(maxlen=20)
db_history_3s = deque(maxlen=FPS * STATS_WINDOW_SEC)
spec_history_3s = deque(maxlen=FPS * STATS_WINDOW_SEC)
global_max_energy = 1e-6
freq_bins_global = None

# === 合成器状态 ===
# 使用梅尔频率中心点
mel_freqs = librosa.mel_frequencies(n_mels=NUM_BINS, fmin=25, fmax=3000)
bin_center_freqs = mel_freqs  # 直接使用梅尔频率中心点
phase_accumulators = np.random.rand(NUM_BINS) * 2 * np.pi

def clear_console():
    os.system('cls' if os.name == 'nt' else 'clear')

def db_from_amplitude(signal):
    rms = np.sqrt(np.mean(signal ** 2))
    if rms <= 0:
        return -np.inf
    return 20 * np.log10(rms)

def compute_spectrum_bins(signal, sample_rate, num_bins=NUM_BINS):
    """
    使用梅尔尺度计算频谱能量
    """
    n = len(signal)
    # 计算FFT
    fft_data = np.abs(np.fft.rfft(signal))[:n // 2]
    freqs = np.fft.rfftfreq(n, 1 / sample_rate)[:n // 2]
    
    # 生成梅尔频率边界 (使用 librosa)
    min_freq = 25
    max_freq = 3000
    mel_bins = librosa.mel_frequencies(n_mels=num_bins + 1, fmin=min_freq, fmax=max_freq)
    
    # 计算每个梅尔bin的能量（使用平均值，更稳定）
    bin_energies = []
    for i in range(num_bins):
        low, high = mel_bins[i], mel_bins[i + 1]
        mask = (freqs >= low) & (freqs < high)
        if np.any(mask):
            # 使用平均值，避免单个强频率主导
            energy = np.mean(fft_data[mask])
        else:
            energy = 0
        bin_energies.append(energy)
    
    return np.array(bin_energies), mel_bins

def generate_robot_audio(spec, prev_phases):
    """根据频谱生成机器人声音，返回音频数据和新的相位"""
    # 归一化
    if global_max_energy > 0:
        amplitudes = spec / global_max_energy
    else:
        amplitudes = np.zeros(NUM_BINS)
    amplitudes = np.clip(amplitudes, 0, 1)
    
    # 生成正弦波
    t = np.arange(CHUNK) / RATE
    synth_signal = np.zeros(CHUNK, dtype=np.float32)
    current_phases = prev_phases.copy()
    
    for i in range(NUM_BINS):
        freq = bin_center_freqs[i]
        amp = amplitudes[i]
        
        if amp > 0.01:
            phase_increment = 2 * np.pi * freq / RATE
            phases = current_phases[i] + np.arange(CHUNK) * phase_increment
            synth_signal += amp * np.sin(phases)
            current_phases[i] = phases[-1] % (2 * np.pi)
    
    # 归一化输出
    max_out = np.max(np.abs(synth_signal))
    if max_out > 0:
        synth_signal = synth_signal / max_out * 0.3 * 32767
    
    return synth_signal.astype(np.int16).tobytes(), current_phases

def generate_freq_axis(freq_bins, num_bins, bin_width):
    """生成频率区间统计轴标签（显示频率范围）"""
    total_width = num_bins * bin_width
    freq_line = [' '] * total_width
    
    # 选择关键区间显示（显示频率范围）
    key_indices = [0, num_bins // 4, num_bins // 2, 3 * num_bins // 4, num_bins - 1]
    for idx in key_indices:
        if idx < len(freq_bins) - 1:
            low_freq = freq_bins[idx]
            high_freq = freq_bins[idx + 1]
            
            # 生成区间标签（例如：0.5-1k 或 100-200）
            if high_freq >= 1000:
                label = f"{low_freq/1000:.1f}-{high_freq/1000:.1f}k"
            else:
                label = f"{low_freq:.0f}-{high_freq:.0f}"
            
            # 限制标签长度
            if len(label) > bin_width * 2:
                label = f"{low_freq/1000:.1f}k" if high_freq >= 1000 else f"{low_freq:.0f}"
            
            pos = idx * bin_width
            pos = min(pos, total_width - len(label))
            for i, c in enumerate(label):
                if pos + i < total_width:
                    freq_line[pos + i] = c
    return ''.join(freq_line)

def calculate_db_statistics(db_history_3s):
    """计算3秒窗口的dB统计"""
    if len(db_history_3s) == 0:
        return -np.inf, -np.inf
    
    db_array = np.array(list(db_history_3s))
    db_array = db_array[np.isfinite(db_array)]  # 移除无效值
    
    if len(db_array) == 0:
        return -np.inf, -np.inf
    
    peak_db = np.max(db_array)
    avg_db = np.mean(db_array)
    
    return peak_db, avg_db

def processing_worker():
    """后台线程：负责计算频谱 + 生成音频 + 绘制界面"""
    global global_max_energy, freq_bins_global, history_lines, db_history, db_history_3s, spec_history_3s
    global phase_accumulators
    
    last_update = 0
    update_interval = 1.0 / FPS

    while not stop_flag:
        current_time = time.time()
        
        if current_time - last_update < update_interval:
            time.sleep(0.001)
            continue

        # 获取最新音频数据
        data = None
        while len(audio_buffer) > 0:
            data = audio_buffer.popleft()
        
        if data is None:
            continue

        # === 核心计算 ===
        signal = np.frombuffer(data, dtype=np.int16).astype(np.float32)
        current_db = db_from_amplitude(signal)
        db_history.append(current_db)
        db_history_3s.append(current_db)

        if current_db < SILENCE_DB_THRESHOLD:
            spec = np.zeros(NUM_BINS)
            spec_history_3s.append(np.zeros(NUM_BINS))
            robot_audio, phase_accumulators = generate_robot_audio(spec, phase_accumulators)
            playback_queue.append(robot_audio)
        else:
            spec, freq_bins = compute_spectrum_bins(signal, RATE, NUM_BINS)
            freq_bins_global = freq_bins
            frame_max = np.max(spec)
            if frame_max > global_max_energy:
                global_max_energy = frame_max
            spec_history_3s.append(spec.copy())
            
            robot_audio, phase_accumulators = generate_robot_audio(spec, phase_accumulators)
            playback_queue.append(robot_audio)

        # 16个视觉密度递增的字符（大小相同）
        VISUAL_ENERGY_CHARS = [
            '-',  # 空格代表最低能量
            '·',  # 中间点
            '·',  # 中间点
            '∙',  # 子弹点
            '∙',  # 子弹点
            '•',  # 圆点
            '○',  # 空心圆
            '○',  # 空心圆
            '◌',  # 虚线圆
            '◌',  # 虚线圆
            '◍',  # 半填充圆
            '◍',  # 半填充圆
            '●',  # 实心圆
            '●',  # 实心圆
            '●',  # 实心圆
            '◉',  # 带点圆
            '◉',  # 带点圆
            '#',  # 井号
            '#',  # 井号
        ]
        
        # === 界面渲染 ===
        if global_max_energy == 0:
            norm = np.zeros_like(spec)
        else:
            # 使用对数刻度显示，更适合梅尔频谱
            norm = (len(VISUAL_ENERGY_CHARS)-1) * np.log1p(spec) / np.log1p(global_max_energy)
        norm = np.clip(norm, 0, (len(VISUAL_ENERGY_CHARS)-1)).astype(int)

        # 每个bin只显示一个字符
        line = ''.join(VISUAL_ENERGY_CHARS[v] for v in norm)
        history_lines.append(line)
        
        # 计算3秒窗口统计信息
        peak_db_3s, avg_db_3s = calculate_db_statistics(db_history_3s)
        
        # 计算3秒窗口内每个bin的归一化能量（使用最大F归一化）
        bin_energies_3s = {}
        if len(spec_history_3s) > 0 and global_max_energy > 0:
            # 计算3秒窗口内每个bin的平均能量
            avg_bin_energies = np.mean(list(spec_history_3s), axis=0)
            
            # 归一化
            normalized_energies = (len(VISUAL_ENERGY_CHARS)-1) * np.log1p(avg_bin_energies) / np.log1p(global_max_energy)
            normalized_energies = np.clip(normalized_energies, 0, (len(VISUAL_ENERGY_CHARS)-1)).astype(int)
            
            # 为每个bin创建对应的显示值
            for i in range(NUM_BINS):
                bin_energies_3s[f'bin_{i}'] = normalized_energies[i]
        
        # 相位显示（简化）
        phase_display = " ".join([f"{int(np.degrees(p)%360):3d}" for p in phase_accumulators[:20]]) + "..."

        # 构建输出缓冲
        output_buffer = []
        total_width = NUM_BINS  # 现在每个bin只占1个字符宽度
        
        output_buffer.append("🎵 频率区间能量统计分析 + 机器人音效")
        output_buffer.append("=" * min(total_width, 80))
        
        # 频谱历史（表格部分）- 现在每行正好60个字符
        for l in list(history_lines):
            output_buffer.append(l)
        output_buffer.append("=" * min(total_width, 80))
        
        # 添加最近3秒的归一化能量行
        if bin_energies_3s:
            # 创建一行显示每个bin的归一化能量
            energy_line = ''
            for i in range(NUM_BINS):
                val = bin_energies_3s[f'bin_{i}']
                energy_line += VISUAL_ENERGY_CHARS[val]
            output_buffer.append(energy_line)
        
        # 频率区间统计轴
        if freq_bins_global is not None:
            # 调整轴标签的显示宽度
            freq_axis = generate_freq_axis(freq_bins_global, NUM_BINS, 1)  # bin_width改为1
            output_buffer.append(freq_axis)
        
        output_buffer.append("")  # 空行分隔
        
        # 显示3秒窗口的dB统计
        output_buffer.append(f"📊 3秒窗口统计 (最近 {STATS_WINDOW_SEC} 秒):")
        output_buffer.append(f"   - 峰值 dB: {peak_db_3s:6.2f} dB")
        output_buffer.append(f"   - 平均 dB: {avg_db_3s:6.2f} dB")

        output_buffer.append(f"\n🔄 队列状态: {len(playback_queue)} | 最大能量: {global_max_energy:.2f}")
        output_buffer.append("（按 Enter 停止）")

        clear_console()
        sys.stdout.write('\n'.join(output_buffer))
        sys.stdout.flush()

        last_update = current_time

def audio_record_callback(in_data, frame_count, time_info, status):
    audio_buffer.append(in_data)
    return (None, pyaudio.paContinue)

def playback_callback(in_data, frame_count, time_info, status):
    if playback_queue:
        data = playback_queue.popleft()
        return (data, pyaudio.paContinue)
    else:
        return (np.zeros(CHUNK, dtype=np.int16).tobytes(), pyaudio.paContinue)

def main():
    global stop_flag
    print("按下 [Enter] 开始频率区间能量统计...")
    input()

    p = pyaudio.PyAudio()

    # 录音流
    record_stream = p.open(
        format=pyaudio.paInt16,
        channels=1,
        rate=RATE,
        frames_per_buffer=CHUNK,
        input=True,
        stream_callback=audio_record_callback
    )

    # 播放流
    play_stream = p.open(
        format=pyaudio.paInt16,
        channels=1,
        rate=RATE,
        frames_per_buffer=CHUNK,
        output=True,
        stream_callback=playback_callback
    )

    # 预填充 - 使用空格字符串，长度正好为NUM_BINS
    zero_line = ' ' * NUM_BINS
    for _ in range(HISTORY_LINES):
        history_lines.append(zero_line)

    # 启动线程
    process_thread = threading.Thread(target=processing_worker, daemon=True)
    process_thread.start()

    record_stream.start_stream()
    play_stream.start_stream()
    print("🎙️  监听已启动... (频率区间统计模式)")

    try:
        input()
    except KeyboardInterrupt:
        pass

    stop_flag = True
    record_stream.stop_stream()
    play_stream.stop_stream()
    record_stream.close()
    play_stream.close()
    p.terminate()
    print("\n⏹️  已停止。")

if __name__ == "__main__":
    main()
