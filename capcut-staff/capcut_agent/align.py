"""원본 ↔ 편집본 오디오 정렬 — 편집본의 각 구간이 원본 어디였는지 되찾는다.

편집본은 원본 조각들을 이어붙인 것이다. 그래서 편집본을 앞에서부터 훑으며
"이 조각이 원본 몇 초 지점인가"를 찾고, 그 대응이 끊기는 지점이 곧 **컷**이다.

이렇게 얻은 컷 목록으로 다음을 알 수 있다.
  · 어디를 잘라냈는가 (대본과 겹치면 '무엇을' 잘라냈는지까지)
  · 얼마나 바짝 자르는가 (남긴 무음 길이 → pad, min_silence 역산)
  · 평균 컷 길이, 컷 빈도

해상도·코덱·볼륨이 달라도 상관없다. 오디오의 **모양**만 본다.
음악을 새로 깔았거나 목소리를 크게 바꿨다면 정확도가 떨어진다.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

US = 1_000_000
SR = 16000          # 특징 추출용 샘플레이트
FPS = 100           # 초당 특징 프레임 수 (10ms 간격)
BANDS = 16          # 주파수 밴드 수


# ── 특징 추출 ────────────────────────────────────────────────────
def _read_audio(path: str | Path, sr: int = SR) -> np.ndarray:
    """모노 float32 파형. ffmpeg 이 리샘플/다운믹스까지 처리한다."""
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path),
         "-vn", "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"],
        capture_output=True,
    )
    if r.returncode != 0:
        raise RuntimeError(f"오디오 추출 실패: {r.stderr.decode('utf-8', 'replace')[-300:]}")
    return np.frombuffer(r.stdout, dtype=np.int16).astype(np.float32) / 32768.0


@dataclass
class Track:
    """정렬에 쓰는 오디오 표현."""
    feat: np.ndarray       # (프레임, 밴드) 프레임별 정규화된 로그 스펙트럼
    db: np.ndarray         # (프레임,) 프레임 세기 dB
    floor: float           # 이보다 조용하면 '무음' 으로 본다

    def __len__(self) -> int:
        return len(self.feat)

    @property
    def quiet(self) -> np.ndarray:
        return self.db < self.floor


def features(path: str | Path, sr: int = SR, fps: int = FPS,
             bands: int = BANDS, quiet_below_db: float = 32.0) -> Track:
    """(프레임, 밴드) 로그 스펙트럼 + 프레임 세기.

    편집본은 재인코딩·볼륨 조정을 거쳤을 수 있다. 절대 크기를 보면 안 되고
    스펙트럼의 **모양**을 봐야 한다 → 프레임마다 평균을 뺀다.

    다만 그렇게 정규화하면 **무음 구간이 잡음으로 변한다.** 무음끼리는 절대
    닮을 수가 없어서, 무음을 불일치로 세면 말과 말 사이마다 정합이 끊긴다.
    그래서 세기를 따로 들고 다니며 "둘 다 조용한 구간"은 판단을 보류한다.

    quiet_below_db: 그 파일에서 가장 큰 소리보다 몇 dB 아래를 무음으로 볼지.
                    절대값이 아니라 상대값이라 볼륨을 키운 편집본에도 통한다.
    """
    x = _read_audio(path, sr)
    hop = sr // fps
    win = hop * 2
    n = (len(x) - win) // hop
    if n <= 0:
        raise RuntimeError("오디오가 너무 짧습니다")

    # 프레임으로 자르기 (stride trick — 복사 없이 뷰만 만든다)
    frames = np.lib.stride_tricks.as_strided(
        x, shape=(n, win), strides=(x.strides[0] * hop, x.strides[0]))
    windowed = frames * np.hanning(win)
    spec = np.abs(np.fft.rfft(windowed, axis=1))

    rms = np.sqrt((frames ** 2).mean(axis=1))
    db = 20.0 * np.log10(rms + 1e-9)
    floor = float(np.percentile(db, 95) - quiet_below_db)

    # 로그 간격 밴드로 묶기 — 사람 귀에 가깝고 재인코딩에 덜 흔들린다
    edges = np.unique(np.geomspace(2, spec.shape[1] - 1, bands + 1).astype(int))
    out = np.stack([spec[:, a:b].mean(axis=1)
                    for a, b in zip(edges[:-1], edges[1:])], axis=1)
    out = np.log1p(out * 100.0)
    out -= out.mean(axis=1, keepdims=True)      # 프레임별 음량 정규화
    return Track(feat=np.ascontiguousarray(out, dtype=np.float32),
                 db=db.astype(np.float32), floor=floor)


# ── 정합 ────────────────────────────────────────────────────────
def _ncc(orig: np.ndarray, probe: np.ndarray) -> np.ndarray:
    """probe 를 orig 위에서 미끄러뜨리며 정규화 상관을 구한다 (FFT 로 한 번에)."""
    T, B = orig.shape
    P = probe.shape[0]
    if P > T:
        return np.array([])
    n = 1
    while n < T + P:
        n <<= 1

    fo = np.fft.rfft(orig, n=n, axis=0)
    fp = np.fft.rfft(probe[::-1], n=n, axis=0)
    corr = np.fft.irfft(fo * fp, n=n, axis=0)[:, :B].sum(axis=1)
    num = corr[P - 1:T]                          # 오프셋 0..T-P

    sq = (orig ** 2).sum(axis=1)
    cs = np.concatenate([[0.0], np.cumsum(sq)])
    win_e = cs[P:T + 1] - cs[:T - P + 1]
    den = np.sqrt(np.maximum(win_e, 1e-9)) * np.sqrt(max((probe ** 2).sum(), 1e-9))
    return num / den


def _run_length(orig: Track, edit: Track, o: int, pos: int,
                thresh: float, win: int) -> int:
    """오프셋 o 를 유지한 채 몇 프레임이나 계속 맞아떨어지는지."""
    m = min(len(orig) - o, len(edit) - pos)
    if m <= 0:
        return 0
    a, b = orig.feat[o:o + m], edit.feat[pos:pos + m]
    num = (a * b).sum(axis=1)
    den = np.linalg.norm(a, axis=1) * np.linalg.norm(b, axis=1) + 1e-9
    sim = num / den

    # 둘 다 조용한 프레임은 판단 보류 — 무음끼리는 닮을 수가 없다.
    # 한쪽만 조용하면 진짜 불일치다 (말을 잘라낸 자리).
    both_quiet = orig.quiet[o:o + m] & edit.quiet[pos:pos + m]
    sim = np.where(both_quiet, 1.0, sim)

    if m <= win:
        return m if sim.mean() >= thresh else 0
    # 앞으로 win 프레임 평균이 처음으로 무너지는 지점
    cs = np.concatenate([[0.0], np.cumsum(sim)])
    fwd = (cs[win:] - cs[:-win]) / win
    bad = np.flatnonzero(fwd < thresh)
    return m if bad.size == 0 else int(bad[0])


@dataclass
class Match:
    """편집본 [edit_start, edit_end) 가 원본 [orig_start, orig_end) 였다. 단위 µs."""
    orig_start: int
    orig_end: int
    edit_start: int
    edit_end: int
    score: float

    @property
    def dur(self) -> int:
        return self.orig_end - self.orig_start


@dataclass
class AlignResult:
    matches: list[Match]
    orig_us: int
    edit_us: int
    unmatched_us: int          # 원본에서 찾지 못한 편집본 구간 (음악/재녹음 등)

    @property
    def removed_us(self) -> int:
        return self.orig_us - sum(m.dur for m in self.matches)

    def gaps(self) -> list[tuple[int, int]]:
        """원본에서 잘려나간 구간 목록."""
        kept = sorted((m.orig_start, m.orig_end) for m in self.matches)
        out, cur = [], 0
        for a, b in kept:
            if a > cur:
                out.append((cur, a))
            cur = max(cur, b)
        if cur < self.orig_us:
            out.append((cur, self.orig_us))
        return out


def _back_length(orig: Track, edit: Track, o: int, pos: int,
                 limit: int, thresh: float, win: int) -> int:
    """오프셋을 유지한 채 **뒤로** 몇 프레임이나 맞아떨어지는지.

    탐침은 말소리에서 시작해야 정확하다. 그런데 조각의 진짜 시작은 그 앞
    무음일 수 있어서, 말에서 찾은 뒤 앞쪽으로 되짚어 올라간다.
    """
    L = min(limit, o, pos)
    if L <= 0:
        return 0
    a = orig.feat[o - L:o][::-1]
    b = edit.feat[pos - L:pos][::-1]
    num = (a * b).sum(axis=1)
    den = np.linalg.norm(a, axis=1) * np.linalg.norm(b, axis=1) + 1e-9
    sim = num / den
    bq = (orig.quiet[o - L:o][::-1]) & (edit.quiet[pos - L:pos][::-1])
    sim = np.where(bq, 1.0, sim)

    if L <= win:
        return L if sim.mean() >= thresh else 0
    cs = np.concatenate([[0.0], np.cumsum(sim)])
    fwd = (cs[win:] - cs[:-win]) / win
    bad = np.flatnonzero(fwd < thresh)
    return L if bad.size == 0 else int(bad[0])


def align(
    orig: Track,
    edit: Track,
    probe_s: float = 4.0,
    min_probe_s: float = 1.0,
    min_seg_s: float = 0.25,
    sim_thresh: float = 0.55,
    score_thresh: float = 0.45,
    slack_s: float = 1.0,
    fps: int = FPS,
    on_progress=None,
) -> AlignResult:
    """편집본을 앞에서부터 훑으며 원본 대응 구간을 찾는다.

    min_probe_s: 이보다 짧은 탐침은 쓰지 않는다. 짧은 조각은 긴 원본 어딘가에
                 우연히 맞아버려서, 정렬이 앞뒤로 튀는 원인이 된다.
    slack_s:     순방향 후보를 찾을 때 직전 구간 끝에서 얼마나 뒤로 봐줄지.
    """
    E = len(edit)
    matches: list[Match] = []
    unmatched = 0
    pos = 0
    last_orig_end = 0          # 순방향 진행을 유도하기 위한 커서
    win = max(8, fps // 8)
    min_seg = max(2, int(min_seg_s * fps))

    def us(frames: int) -> int:
        return int(frames * US / fps)

    while pos < E - min_seg:
        # 탐침은 말소리에서 시작해야 한다. 무음 구간을 상관시키면 아무 데나 맞는다.
        probe_start = pos
        while probe_start < E and edit.quiet[probe_start]:
            probe_start += 1
        if probe_start >= E - min_seg:
            break

        # 탐침을 줄여가며 시도한다. 짧은 조각은 긴 탐침이 컷을 넘어가서 못 찾는다.
        #
        # 다만 **짧은 탐침은 변별력이 없다.** 9분짜리 원본에서 0.3초 조각은
        # 아무 데나 우연히 맞는다. 그래서 (1) 최소 길이를 두고 (2) 짧을수록
        # 더 높은 점수를 요구한다.
        found = None
        for p_s in (probe_s, probe_s / 2, probe_s / 4, min_probe_s):
            P = int(p_s * fps)
            if P < int(min_probe_s * fps):
                break
            if probe_start + P > E:
                P = E - probe_start
            if P < int(min_probe_s * fps):
                break

            sc = _ncc(orig.feat, edit.feat[probe_start:probe_start + P])
            if sc.size == 0:
                break

            # 탐침이 짧을수록 문턱을 올린다
            shortness = 1.0 - min(1.0, p_s / probe_s)
            need = score_thresh + shortness * 0.30

            g = int(np.argmax(sc))
            best = float(sc[g])

            # 편집은 순서를 뒤집지 않는다. 직전 구간 뒤쪽을 우선해서 본다.
            # 전역 최고점과 큰 차이가 없으면 순방향 후보를 택한다.
            lo = max(0, last_orig_end - int(slack_s * fps))
            o, score = g, best
            if lo < len(sc):
                c = lo + int(np.argmax(sc[lo:]))
                if float(sc[c]) >= max(need, best - 0.06):
                    o, score = c, float(sc[c])

            if score >= need:
                found = (o, score)
                break

        if found is None:
            # 원본에 없는 소리 (배경음악·재녹음·삽입 클립). 한 칸 건너뛴다.
            unmatched += us(min_seg)
            pos = probe_start + min_seg
            continue

        o, score = found
        # 말소리에서 찾았으니 앞뒤로 넓힌다
        back = _back_length(orig, edit, o, probe_start,
                            probe_start - pos, sim_thresh, win)
        seg_o, seg_e = o - back, probe_start - back
        ln = back + _run_length(orig, edit, o, probe_start, sim_thresh, win)
        if ln < min_seg:
            unmatched += us(min_seg)
            pos = probe_start + min_seg
            continue

        if seg_e > pos:
            unmatched += us(seg_e - pos)   # 되짚어도 못 붙인 앞부분

        matches.append(Match(
            orig_start=us(seg_o), orig_end=us(seg_o + ln),
            edit_start=us(seg_e), edit_end=us(seg_e + ln),
            score=score,
        ))
        pos = seg_e + ln
        last_orig_end = seg_o + ln
        if on_progress:
            on_progress(pos / E)

    # 편집본에서 이어진 조각이 원본에서도 이어져 있으면 하나로 합친다
    merged: list[Match] = []
    for m in matches:
        if merged:
            last = merged[-1]
            if (abs(m.orig_start - last.orig_end) <= us(2)
                    and abs(m.edit_start - last.edit_end) <= us(2)):
                last.orig_end = m.orig_end
                last.edit_end = m.edit_end
                last.score = min(last.score, m.score)
                continue
        merged.append(m)

    return AlignResult(
        matches=merged,
        orig_us=us(len(orig)),
        edit_us=us(E),
        unmatched_us=unmatched,
    )


def align_files(original: str | Path, edited: str | Path, **kw) -> AlignResult:
    return align(features(original), features(edited), **kw)
