import torch
import torch.nn as nn
import torch.nn.functional as F
import pyaudio
import numpy as np
import threading
import time
import collections
import sys
import os

from SincConv import SincConv
# ==========================================
# 2. 简化版鹦鹉模型（稳定重建）
# ==========================================
class TinyParrotNet(nn.Module):
    def __init__(self, sample_rate=16000):
        super().__init__()
        self.sinc = SincConv(
            in_channels=1,
            out_channels=60,
            kernel_size=101,
            padding=50,
            sample_rate=sample_rate,
            min_low_hz=50,
            min_band_hz=50
        )
        self.encoder = nn.Sequential(
            nn.Conv1d(60, 128, kernel_size=7, padding=3),
            nn.BatchNorm1d(128),
            nn.ReLU(),
            nn.Conv1d(128, 64, kernel_size=5, padding=2),
            nn.BatchNorm1d(64),
            nn.ReLU(),
        )
        self.decoder = nn.Sequential(
            nn.Conv1d(64, 128, kernel_size=5, padding=2),
            nn.ReLU(),
            nn.Conv1d(128, 1, kernel_size=7, padding=3),
            nn.Tanh()
        )
        self.gain = nn.Parameter(torch.tensor(2.0))  # 初始放大2倍，可学习

    def forward(self, x):
        feat = self.sinc(x)
        feat = torch.abs(feat)  # 包络检波
        encoded = self.encoder(feat)
        out = self.decoder(encoded)
        out = torch.tanh(out) * self.gain  # 放大
        return out


# ==========================================
# 3. 音频引擎（含 VAD 和修复播放逻辑）
# ==========================================

class AudioEngine:
    def __init__(self, model):
        self.model = model
        self.CHUNK = 1024
        self.FORMAT = pyaudio.paFloat32
        self.CHANNELS = 1
        self.RATE = 16000
        self.DURATION = 3.0  # 固定处理 1 秒语音

        self.buffer_size = int(self.RATE * 3)  # 保留最近 3 秒
        self.input_buffer = collections.deque(maxlen=self.buffer_size)
        self.is_running = True
        self.play_queue = collections.deque()
        self.lock = threading.Lock()
        self.p = pyaudio.PyAudio()
        self.optimizer = torch.optim.Adam(self.model.parameters(), lr=0.0005)
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        print(f">>> 设备: {self.device}")

    def start(self):
        self.stream_in = self.p.open(
            format=self.FORMAT,
            channels=self.CHANNELS,
            rate=self.RATE,
            input=True,
            frames_per_buffer=self.CHUNK,
            stream_callback=self._audio_input_callback,
        )
        self.stream_out = self.p.open(
            format=self.FORMAT,
            channels=self.CHANNELS,
            rate=self.RATE,
            output=True,
            frames_per_buffer=self.CHUNK,
            stream_callback=self._audio_output_callback,
        )
        self.stream_in.start_stream()
        self.stream_out.start_stream()
        print(">>> 音频引擎启动 (全双工)")

    def _audio_input_callback(self, in_data, frame_count, time_info, status):
        data = np.frombuffer(in_data, dtype=np.float32)
        with self.lock:
            self.input_buffer.extend(data)
        return (in_data, pyaudio.paContinue)

    def _audio_output_callback(self, in_data, frame_count, time_info, status):
        with self.lock:
            if self.play_queue:
                chunk = self.play_queue.popleft()
                if len(chunk) < frame_count:
                    chunk = np.pad(chunk, (0, frame_count - len(chunk)))
                elif len(chunk) > frame_count:
                    self.play_queue.appendleft(chunk[frame_count:])
                    chunk = chunk[:frame_count]
                return (chunk.astype(np.float32).tobytes(), pyaudio.paContinue)
            else:
                return (np.zeros(frame_count, dtype=np.float32).tobytes(), pyaudio.paContinue)

    def trigger_repeat(self):
        print("\n>>> 触发复读...")

        # --- VAD：从缓冲区找最近 1 秒内能量最高的片段 ---
        with self.lock:
            win_len = int(self.RATE * self.DURATION)  # ← 动态计算 3 秒长度
            if len(self.input_buffer) < win_len:
                print(">>> 缓冲区不足")
                return
            buffer = np.array(self.input_buffer, dtype=np.float32)
            energies = []
            starts = []
            for i in range(len(buffer) - win_len + 1):
                seg = buffer[i:i+win_len]
                energy = np.sum(seg**2)
                energies.append(energy)
                starts.append(i)
            if not energies:
                return
            best_idx = np.argmax(energies)
            raw_audio = buffer[starts[best_idx]:starts[best_idx] + win_len]
            # === 打印输入音频统计信息 ===
            input_peak = np.max(np.abs(raw_audio))
            input_rms = np.sqrt(np.mean(raw_audio**2))
            print(f">>> 麦克风输入 | 峰值: {input_peak:.6f}, RMS: {input_rms:.6f}")
            # 静音检测
            if np.max(np.abs(raw_audio)) < 0.01:
                print(">>> 检测到静音，跳过")
                return

        # --- 推理 ---
        x = torch.from_numpy(raw_audio).float().unsqueeze(0).unsqueeze(0).to(self.device)
        self.model.eval()
        with torch.no_grad():
            y = self.model(x)
        min_len = min(x.shape[-1], y.shape[-1])
        loss = F.mse_loss(y[:, :, :min_len], x[:, :, :min_len])
        print(f">>> 初始 Loss: {loss.item():.6f}")

        # --- 在线学习（仅当 loss 较高时）---
        if loss.item() > 0.03:
            print(">>> Loss 偏高，开始在线学习...")
            self.model.train()
            
            for step in range(12):
                self.optimizer.zero_grad()
                y_train = self.model(x)
                loss_train = F.mse_loss(y_train[:, :, :min_len], x[:, :, :min_len])
                loss_train.backward()
                torch.nn.utils.clip_grad_norm_(self.model.parameters(), max_norm=1.0)
                self.optimizer.step()
            print(f">>> 学习后 Loss: {loss_train.item():.6f}")
            # 重新推理
            self.model.eval()
            with torch.no_grad():
                y = self.model(x)

        # --- 播放 ---
        output_audio = y.squeeze().cpu().numpy()
        if len(output_audio) != len(raw_audio):
            if len(output_audio) > len(raw_audio):
                output_audio = output_audio[:len(raw_audio)]
            else:
                output_audio = np.pad(output_audio, (0, len(raw_audio) - len(output_audio)))

        # ===== 自动增益：目标峰值 0.9，避免削波 =====
        peak = np.max(np.abs(output_audio))
        if peak > 0 and peak < 0.9:
            output_audio = output_audio * (0.9 / peak)
        else:
            output_audio = np.zeros_like(output_audio)

        output_audio = np.clip(output_audio, -1.0, 1.0).astype(np.float32)
        print(f">>> 播放中... 峰值:{peak:.6f} → 放大后峰值:{np.max(np.abs(output_audio)):.6f}")

        with self.lock:
            self.play_queue.clear()
            for i in range(0, len(output_audio), self.CHUNK):
                self.play_queue.append(output_audio[i:i+self.CHUNK])

    def close(self):
        self.is_running = False
        self.stream_in.stop_stream()
        self.stream_out.stop_stream()
        self.stream_in.close()
        self.stream_out.close()
        self.p.terminate()


# ==========================================
# 4. 主程序
# ==========================================

def main():
    print("初始化鹦鹉模型...")
    model = TinyParrotNet()
    
    if os.path.exists("parrot_last.pth"):
        try:
            model.load_state_dict(torch.load("parrot_last.pth", map_location='cpu'))
            print(">>> 模型加载成功！")
        except Exception as e:
            print(f">>> 加载失败: {e}，使用新模型。")
    
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model.to(device)
    model.eval()

    engine = AudioEngine(model)
    engine.start()

    print("\n========================================")
    print(" 🦜 鹦鹉学舌系统 (修复版)")
    print(" - 说话后按回车，它会复读并学习")
    print(" - 多次重复同一句话，声音会越来越清晰")
    print(" - 输入 'q' 退出")
    print("========================================")

    try:
        while True:
            cmd = input()
            if cmd.lower() == 'q':
                break
            engine.trigger_repeat()
    except KeyboardInterrupt:
        pass

    print("\n正在关闭...")
    engine.close()
    torch.save(model.state_dict(), "parrot_last.pth")
    print("模型已保存。再见！")


if __name__ == "__main__":
    main()
