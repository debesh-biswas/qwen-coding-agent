import numpy as np, wave
SR = 48000; DUR = 21.0; BPM = 120; B = 60 / BPM; N = int(SR * DUR)
rng = np.random.default_rng(7)
def tt(n): return np.arange(n) / SR
def hz(m): return 440 * 2 ** ((m - 69) / 12)
def add(buf, x, at):
    i = int(round(at * SR)); j = min(len(buf), i + len(x))
    if j > i: buf[i:j] += x[:j - i]
def fftfilt(x, lo=None, hi=None):
    X = np.fft.rfft(x); f = np.fft.rfftfreq(len(x), 1 / SR); g = np.ones_like(f)
    if lo: g *= 1 / np.sqrt(1 + (lo / np.maximum(f, 1)) ** 4)
    if hi: g *= 1 / np.sqrt(1 + (f / hi) ** 4)
    return np.fft.irfft(X * g, len(x))
def env(n, a, d):  # attack, exp decay (seconds)
    t = tt(n); return np.minimum(1, t / max(a, 1e-4)) * np.exp(-t / d)

drums = np.zeros(N); bass = np.zeros(N); pad = np.zeros(N); lead = np.zeros(N); fx = np.zeros(N)
CH = [[57, 60, 64], [53, 57, 60], [48, 52, 55], [55, 59, 62]]  # Am F C G
ROOT = [45, 41, 36, 43]
def chord(t): return int(t // (4 * B)) % 4

# pad: soft saw-ish additive, one chord per bar, whole piece
for bar in range(11):
    t0 = bar * 4 * B; n = int(4 * B * SR) + SR // 2; t = tt(n)
    x = np.zeros(n)
    for m in CH[bar % 4]:
        for det in (-0.08, 0.08):
            f = hz(m + det)
            for h in range(1, 7): x += np.sin(2 * np.pi * f * h * t + h) / h ** 1.6
    e = np.minimum(1, t / 0.35) * np.minimum(1, np.maximum(0, (n / SR - t)) / 0.5)
    lvl = 0.018 if t0 < 4 else (0.014 if t0 < 17 else 0.02)
    add(pad, x * e * lvl, t0)
# final ring-out chord (Am add9) at 20.0
n = int(1.0 * SR); t = tt(n); x = sum(np.sin(2 * np.pi * hz(m) * t) for m in (45, 57, 60, 64, 71))
add(pad, x * env(n, .01, .5) * .05, 20.0)

def kick(at, g=1.0):
    n = int(.4 * SR); t = tt(n); f = 45 + 80 * np.exp(-t / .035)
    ph = 2 * np.pi * np.cumsum(f) / SR
    add(drums, np.sin(ph) * np.exp(-t / .16) * .40 * g, at)
def clap(at, g=1.0):
    n = int(.25 * SR); x = fftfilt(rng.standard_normal(n), 900, 5000)
    e = np.exp(-tt(n) / .06); add(drums, x * e * .07 * g, at)
def hat(at, g=1.0, open_=False):
    n = int((.25 if open_ else .06) * SR); x = fftfilt(rng.standard_normal(n), 7000, 14000)
    add(drums, x * np.exp(-tt(n) / (.07 if open_ else .015)) * .05 * g, at)
def bassnote(at, m, length, g=1.0):
    n = int(length * SR); t = tt(n); f = hz(m)
    x = np.sin(2 * np.pi * f * t) + .25 * np.sin(4 * np.pi * f * t) + .08 * np.sin(6 * np.pi * f * t)
    e = np.minimum(1, t / .005) * np.exp(-t / (length * .9)) * np.minimum(1, (n / SR - t) / .02)
    add(bass, x * e * .16 * g, at)
def pluck(at, m, g=1.0, d=.22):
    n = int(.7 * SR); t = tt(n); f = hz(m)
    x = sum(np.sin(2 * np.pi * f * h * t) * np.exp(-t * h * 4) / h for h in range(1, 6))
    add(lead, x * env(n, .003, d) * .12 * g, at)
def bell(at, m, g=1.0):
    n = int(1.6 * SR); t = tt(n); f = hz(m)
    x = np.sin(2 * np.pi * f * t) + .4 * np.sin(2 * np.pi * f * 2.0 * t) * np.exp(-t * 3) + .2 * np.sin(2 * np.pi * f * 3.01 * t) * np.exp(-t * 6)
    add(lead, x * env(n, .002, .45) * .08 * g, at)
def tick(at, g=1.0):
    n = int(.03 * SR); x = fftfilt(rng.standard_normal(n), 2500, 7000)
    add(fx, x * np.exp(-tt(n) / .006) * .05 * g, at)
def whoosh(end, length=.5, g=1.0):
    n = int(length * SR); t = tt(n); x = rng.standard_normal(n)
    # sweep: blend lowpassed -> highpassed across the swell
    a = fftfilt(x, 300, 1500); b = fftfilt(x, 1500, 6000); p = t / length
    y = (a * (1 - p) + b * p) * (p ** 2) * np.minimum(1, (length - t) / .03)
    add(fx, y * .05 * g, end - length)

# --- intro (0-4): quiet hats on 8ths, ticks while typing, riser
for k in range(8):
    hat(k * B + B / 2, .45)
for k in range(int((3.15 - 2.2) / (B / 4)) + 1):
    tick(2.2 + k * B / 4, .8 if k % 2 == 0 else .5)
tick(3.25, 1.0); tick(3.375, .7)
n = int(1.0 * SR); t = tt(n); x = fftfilt(rng.standard_normal(n), 400, 3000)
add(fx, x * (t / 1.0) ** 3 * .06, 3.0)
for k in range(4): bassnote(3.0 + k * B / 4, 43, B / 4 * .9, .25 + .2 * k)  # G pickup, swelling

# --- groove (4-17)
for k in range(int((17 - 4) / B)):
    at = 4 + k * B
    kick(at, 1.0 if k % 2 == 0 else .85)
    if k % 2 == 1: clap(at)
    hat(at + B / 2, .9, open_=(k % 4 == 3))
    hat(at + B / 4, .35); hat(at + 3 * B / 4, .35)
for k in range(int((17 - 4) / (B / 2))):  # bass 8ths
    at = 4 + k * B / 2; r = ROOT[chord(at)]
    bassnote(at, r + (12 if k % 4 == 3 else 0), B / 2 * .85, 1 if k % 2 == 0 else .7)
# chime on the drop
bell(4.0, 81, 1.1); bell(4.0, 88, .6); bell(4.25, 76, .5)
# arp lead 6-15: chord tones, 8ths
for k in range(int((15 - 6) / (B / 2))):
    at = 6 + k * B / 2; c = CH[chord(at)]
    m = (c + [c[0] + 12])[[0, 1, 2, 3, 2, 1, 3, 2][k % 8]] + 12
    pluck(at, m, .55 if at < 9 else .7)
# programming bars ticks + winner
for j in range(6): tick(9.375 + j * B / 4, .7)
bell(10.75, 76, .9); bell(10.75, 81, .6)
# debugging: report key ticks (on 16ths) 13.25..14.5
for i in range(12): tick(13.25 + i * B / 4, .35)
bell(13.0, 76, .5)
# fill 16-17: snare/clap 16ths crescendo
for k in range(8): clap(16 + k * B / 4, .3 + .1 * k)
# whooshes into cuts
for c in (6.0, 9.0, 12.0, 15.0, 17.0): whoosh(c, .45, .9)

# --- outro (17-21): breakdown, accent at 18, soft groove 18-20, ring-out
kick(17.0, .9); bassnote(17.0, 45, 1.0, .9)
kick(18.0, 1.0); bassnote(18.0, 41, 2.0, .9); bell(18.0, 81, .8); bell(18.0, 76, .5)
for k in range(4):
    hat(18 + k * B + B / 2, .5)
    if k % 2 == 1: kick(18 + k * B, .6)
for k in range(4): hat(19 + k * B / 2 + B / 4, .25)
kick(20.0, .9); bassnote(20.0, 33, 1.0, .9)
for m, d in zip((69, 72, 76, 81), (0, .125, .25, .375)): pluck(20.0 + d, m, .6, .35)

# --- mix
def reverb(x, secs=1.4, wet=.18):
    n = int(secs * SR); ir = rng.standard_normal(n) * np.exp(-tt(n) / (secs / 5)); ir = fftfilt(ir, 200, 6000)
    L = 1 << int(np.ceil(np.log2(len(x) + n)))
    y = np.fft.irfft(np.fft.rfft(x, L) * np.fft.rfft(ir, L), L)[:len(x)]
    return x + wet * y / np.max(np.abs(y)) * np.max(np.abs(x))
# sidechain duck pad+bass under kicks after the drop
duck = np.ones(N)
for k in range(int((17 - 4) / B)):
    i = int((4 + k * B) * SR); n = int(.25 * SR); duck[i:i + n] = np.minimum(duck[i:i + n], 1 - .45 * np.exp(-tt(n) / .08))
mus = drums + bass * duck + reverb(pad, 2.0, .35) * duck + reverb(lead, 1.4, .3)
fxw = reverb(fx, 1.2, .25)
mix = fftfilt(mus + fxw * .9, 30, 16000)
fade = np.ones(N); fn = int(.6 * SR); fade[-fn:] = np.linspace(1, 0, fn) ** 1.5
mix *= fade
mix = np.tanh(mix * 1.6) / 1.6
mix *= 0.89 / np.max(np.abs(mix))
# light stereo: slight delay on reverb-ish parts via small offset
l = mix; r = np.roll(mix, int(.0004 * SR)) * .98 + mix * .02
st = np.stack([l, r], 1)
with wave.open('music.wav', 'wb') as w:
    w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
    w.writeframes((st * 32767).astype('<i2').tobytes())
print('ok', N / SR, {k: round(float(np.sqrt(np.mean(v**2))),4) for k,v in dict(drums=drums,bass=bass,pad=pad,lead=lead,fx=fx).items()})
