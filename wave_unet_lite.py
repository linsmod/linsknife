"""
Wave-U-Net 鹦鹉学舌系统 - 轻量版
轻量化波形重建神经网络

优化措施:
1. 减少网络深度: 4层→3层
2. 减少通道数: 512→256
3. 缩小卷积核: 15→7
4. 简化残差块: 去掉膨胀卷积
5. 简化损失函数: 单分辨率STFT
6. 优化推理速度
"""

import torch
import torch.nn as nn
import torch.nn.functional as F
import pyaudio
import numpy as np
import threading
import collections


# ==========================================
# 1. 轻量残差块
# ==========================================
class LiteResBlock(nn.Module):
    """轻量残差块，去掉膨胀卷积"""
    def __init__(self, channels):
        super().__init__()
        self.conv1 = nn.Conv1d(channels, channels, kernel_size=5, padding=2)
        self.conv2 = nn.Conv1d(channels, channels, kernel_size=5, padding=2)
        
    def forward(self, x):
        residual = x
        out = F.leaky_relu(self.conv1(x), 0.2)
        out = self.conv2(out)
        return F.leaky_relu(out + residual, 0.2)


# ==========================================
# 2. 轻量编码器块
# ==========================================
class LiteEncoderBlock(nn.Module):
    """轻量下采样编码块"""
    def __init__(self, in_ch, out_ch):
        super().__init__()
        self.conv = nn.Conv1d(in_ch, out_ch, kernel_size=7, stride=2, padding=3)
        self.res = LiteResBlock(out_ch)
        
    def forward(self, x):
        x = F.leaky_relu(self.conv(x), 0.2)
        x = self.res(x)
        return x


# ==========================================
# 3. 轻量解码器块
# ==========================================
class LiteDecoderBlock(nn.Module):
    """轻量上采样解码块"""
    def __init__(self, in_ch, out_ch, skip_ch):
        super().__init__()
        self.up = nn.ConvTranspose1d(in_ch, out_ch, kernel_size=7, stride=2, padding=3, output_padding=1)
        self.conv = nn.Conv1d(out_ch + skip_ch, out_ch, kernel_size=7, padding=3)
        self.res = LiteResBlock(out_ch)
        
    def forward(self, x, skip):
        x = F.leaky_relu(self.up(x), 0.2)
        # 处理尺寸不匹配
        if x.shape[-1] != skip.shape[-1]:
            x = F.interpolate(x, size=skip.shape[-1], mode='linear', align_corners=False)
        x = torch.cat([x, skip], dim=1)
        x = F.leaky_relu(self.conv(x), 0.2)
        x = self.res(x)
        return x


# ==========================================
# 4. 轻量 Wave-U-Net
# ==========================================
class LiteWaveUNet(nn.Module):
    """
    轻量级 U-Net 波形重建网络
    
    特点:
    - 3层编码器/解码器
    - 通道数: 24→48→96→192
    - 单残差块瓶颈层
    - 参数量减少约70%
    """
    def __init__(self, sample_rate=16000):
        super().__init__()
        self.version = "LiteWaveUNetV1"
        self.sample_rate = sample_rate
        
        # 输入卷积
        self.input_conv = nn.Conv1d(1, 24, kernel_size=7, padding=3)
        
        # 编码器路径 (3层)
        self.enc1 = LiteEncoderBlock(24, 48)    # /2
        self.enc2 = LiteEncoderBlock(48, 96)    # /4
        self.enc3 = LiteEncoderBlock(96, 192)   # /8
        
        # 瓶颈层 (单残差块)
        self.bottleneck = LiteResBlock(192)
        
        # 解码器路径 (3层)
        self.dec3 = LiteDecoderBlock(192, 96, 96)   # *2
        self.dec2 = LiteDecoderBlock(96, 48, 48)    # *2
        self.dec1 = LiteDecoderBlock(48, 24, 24)    # *2
        
        # 输出卷积
        self.output_conv = nn.Sequential(
            nn.Conv1d(24, 8, kernel_size=7, padding=3),
            nn.LeakyReLU(0.2),
            nn.Conv1d(8, 1, kernel_size=7, padding=3),
            nn.Tanh(),
        )
        
    def forward(self, x):
        # 输入: [B, 1, T]
        x0 = F.leaky_relu(self.input_conv(x), 0.2)  # [B, 24, T]
        
        # 编码
        x1 = self.enc1(x0)   # [B, 48, T/2]
        x2 = self.enc2(x1)   # [B, 96, T/4]
        x3 = self.enc3(x2)   # [B, 192, T/8]
        
        # 瓶颈
        x_bottle = self.bottleneck(x3)
        
        # 解码 + 跳跃连接
        d3 = self.dec3(x_bottle, x2)  # [B, 96, T/4]
        d2 = self.dec2(d3, x1)        # [B, 48, T/2]
        d1 = self.dec1(d2, x0)        # [B, 24, T]
        
        # 输出
        out = self.output_conv(d1)
        return out


# ==========================================
# 5. 轻量损失函数
# ==========================================
class LiteLoss(nn.Module):
    """轻量损失: L1 + 单分辨率STFT"""
    def __init__(self, n_fft=1024, hop_length=256):
        super().__init__()
        self.n_fft = n_fft
        self.hop_length = hop_length
        
    def stft_loss(self, pred, target):
        pred_stft = torch.stft(pred.squeeze(1), n_fft=self.n_fft, hop_length=self.hop_length, 
                               win_length=self.n_fft, return_complex=True)
        target_stft = torch.stft(target.squeeze(1), n_fft=self.n_fft, hop_length=self.hop_length,
                                 win_length=self.n_fft, return_complex=True)
        
        pred_mag = torch.abs(pred_stft)
        target_mag = torch.abs(target_stft)
        
        return F.l1_loss(torch.log(pred_mag + 1e-7), torch.log(target_mag + 1e-7))
        
    def forward(self, pred, target):
        min_len = min(pred.shape[-1], target.shape[-1])
        pred = pred[..., :min_len]
        target = target[..., :min_len]
        
        time_loss = F.l1_loss(pred, target)
        stft_loss = self.stft_loss(pred, target)
        
        return time_loss + 0.3 * stft_loss


# ==========================================
# 6. 轻量音频引擎
# ==========================================
class LiteAudioEngine:
    def __init__(self, model):
        self.model = model
        self.CHUNK = 1024
        self.FORMAT = pyaudio.paFloat32
        self.CHANNELS = 1
        self.RATE = 16000
        self.DURATION = 2.0  # 减少到2秒

        self.buffer_size = int(self.RATE * 2)
        self.input_buffer = collections.deque(maxlen=self.buffer_size)
        self.is_running = True
        self.play_queue = collections.deque()
        self.lock = threading.Lock()
        self.p = pyaudio.PyAudio()
        
        # 轻量优化器
        self.optimizer = torch.optim.Adam(self.model.parameters(), lr=0.002)
        
        # 损失函数
        self.criterion = LiteLoss()
        
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.model.to(self.device)
        
        # 统计参数量
        params = sum(p.numel() for p in model.parameters())
        print(f">>> 设备: {self.device}")
        print(f">>> 模型参数量: {params:,} ({params/1e6:.2f}M)")

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
        print(">>> 音频引擎启动 (轻量模式)")

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
            return (np.zeros(frame_count, dtype=np.float32).tobytes(), pyaudio.paContinue)

    def trigger_repeat(self):
        print("\n>>> 触发复读...")

        # VAD: 找能量最高片段
        with self.lock:
            win_len = int(self.RATE * self.DURATION)
            if len(self.input_buffer) < win_len:
                print(">>> 缓冲区不足")
                return
            buffer = np.array(self.input_buffer, dtype=np.float32)
            
            # 滑动窗口找最高能量
            hop = win_len // 2
            best_start = 0
            best_energy = 0
            for i in range(0, len(buffer) - win_len + 1, hop):
                energy = np.sum(buffer[i:i+win_len]**2)
                if energy > best_energy:
                    best_energy = energy
                    best_start = i
                    
            raw_audio = buffer[best_start:best_start + win_len]
            
            input_peak = np.max(np.abs(raw_audio))
            print(f">>> 输入峰值: {input_peak:.4f}")
            
            if input_peak < 0.01:
                print(">>> 静音，跳过")
                return

        # 推理
        x = torch.from_numpy(raw_audio).float().unsqueeze(0).unsqueeze(0).to(self.device)
        
        self.model.eval()
        with torch.no_grad():
            y = self.model(x)
        
        min_len = min(x.shape[-1], y.shape[-1])
        loss = self.criterion(y[:, :, :min_len], x[:, :, :min_len])
        print(f">>> 初始Loss: {loss.item():.4f}")

        # 快速在线学习 (最多10步)
        if loss.item() > 0.01:
            print(">>> 在线学习中...")
            self.model.train()
            
            for step in range(10):
                self.optimizer.zero_grad()
                y_train = self.model(x)
                loss_train = self.criterion(y_train[:, :, :min_len], x[:, :, :min_len])
                loss_train.backward()
                torch.nn.utils.clip_grad_norm_(self.model.parameters(), 1.0)
                self.optimizer.step()
                
                if loss_train.item() < 0.005:
                    break
                    
            print(f">>> 学习后Loss: {loss_train.item():.4f}")
            
            self.model.eval()
            with torch.no_grad():
                y = self.model(x)

        # 后处理
        output_audio = y.squeeze().cpu().numpy()
        
        # 长度对齐
        if len(output_audio) != len(raw_audio):
            output_audio = output_audio[:len(raw_audio)] if len(output_audio) > len(raw_audio) \
                           else np.pad(output_audio, (0, len(raw_audio) - len(output_audio)))

        # 软限幅
        output_audio = np.tanh(output_audio * 1.5).astype(np.float32)
        print(f">>> 输出峰值: {np.max(np.abs(output_audio)):.4f}")

        # 播放
        with self.lock:
            self.play_queue.clear()
            for i in range(0, len(output_audio), self.CHUNK):
                self.play_queue.append(output_audio[i:i+self.CHUNK])

    def close(self):
        self.is_running = False
        try:
            self.stream_in.stop_stream()
            self.stream_out.stop_stream()
            self.stream_in.close()
            self.stream_out.close()
        except:
            pass
        self.p.terminate()


# ==========================================
# 7. 主程序
# ==========================================
def main():
    print("=" * 50)
    print("  Wave-U-Net 鹦鹉学舌系统 - 轻量版")
    print("=" * 50)
    
    # 创建轻量模型
    model = LiteWaveUNet()
    
    # 创建引擎
    engine = LiteAudioEngine(model)
    engine.start()
    
    print("\n按回车键触发复读，按 q 退出...")
    
    try:
        while True:
            cmd = input()
            if cmd.lower() == 'q':
                break
            engine.trigger_repeat()
    except KeyboardInterrupt:
        pass
    finally:
        engine.close()
        print(">>> 已退出")


if __name__ == "__main__":
    main()
