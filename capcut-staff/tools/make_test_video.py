"""검증용 한국어 테스트 영상 생성 (Windows SAPI + ffmpeg).

잔말·NG·재테이크를 **일부러 심어둔** 대본을 읽어 영상으로 만든다.
어디가 잘려야 정답인지 미리 알고 있으므로 파이프라인 정확도를 실제로 채점할 수 있다.

    python tools/make_test_video.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

OUT_DIR = Path(__file__).parent.parent / "samples"
GAP_S = 0.9  # 문장 사이 무음. silencedetect 기본값(0.35s)보다 넉넉하게.

# (대사, 정답라벨) — 정답라벨: keep | filler | ng
SCRIPT: list[tuple[str, str]] = [
    ("안녕하세요. 오늘은 영상 자동 편집에 대해 이야기하겠습니다.", "keep"),
    ("음.", "filler"),
    ("제가 준비한 내용은 세 가지입니다.", "keep"),
    ("첫 번째는 무음을 잘라내는 것입니다.", "ng"),          # 아래 문장의 실패 테이크
    ("첫 번째는 무음을 자동으로 잘라내는 것입니다.", "keep"),
    ("어.", "filler"),
    ("두 번째는 잔말을 제거하는 것입니다.", "keep"),
    ("세 번째는 자, 자막을", "ng"),                        # 말하다 끊김
    ("아 죄송합니다. 다시 할게요.", "ng"),                  # NG 마커
    ("세 번째는 자막을 자동으로 넣는 것입니다.", "keep"),
    ("그럼 바로 시작하겠습니다.", "keep"),
]

PS_TTS = r"""
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$ko = $s.GetInstalledVoices() | Where-Object {{ $_.VoiceInfo.Culture.Name -eq 'ko-KR' }} |
      Select-Object -First 1
if (-not $ko) {{ Write-Error 'ko-KR 음성이 없습니다'; exit 1 }}
$s.SelectVoice($ko.VoiceInfo.Name)
$s.Rate = 0
$lines = Get-Content -Path '{jsonp}' -Encoding UTF8 | ConvertFrom-Json
for ($i = 0; $i -lt $lines.Count; $i++) {{
  $p = Join-Path '{outdir}' ("line_{{0:d2}}.wav" -f $i)
  $s.SetOutputToWaveFile($p)
  $s.Speak($lines[$i])
}}
$s.Dispose()
"""


def wav_dur(p: Path) -> float:
    with wave.open(str(p), "rb") as w:
        return w.getnframes() / w.getframerate()


def main() -> int:
    if sys.platform != "win32":
        print("이 스크립트는 Windows SAPI 를 씁니다.", file=sys.stderr)
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="cctest_"))

    lines = [t for t, _ in SCRIPT]
    jsonp = tmp / "lines.json"
    jsonp.write_text(json.dumps(lines, ensure_ascii=False), encoding="utf-8")

    ps1 = tmp / "tts.ps1"
    ps1.write_text(PS_TTS.format(jsonp=jsonp.as_posix(), outdir=tmp.as_posix()),
                   encoding="utf-8-sig")

    print("음성 합성 중…")
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                        "-File", str(ps1)], capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout, r.stderr, file=sys.stderr)
        return 1

    wavs = sorted(p for p in tmp.glob("line_*.wav") if not p.stem.endswith("_n"))
    if len(wavs) != len(SCRIPT):
        print(f"[에러] wav {len(wavs)}개 / 대사 {len(SCRIPT)}개", file=sys.stderr)
        return 1

    # concat 데먹서는 리샘플링을 하지 않는다. 레이트가 섞이면 타임라인이
    # 통째로 밀려서 정답 파일이 무의미해진다 → 먼저 전부 같은 규격으로 맞춘다.
    sr = 24000
    #
    # 동시에 각 대사의 앞뒤 무음을 떼어낸다. SAPI 는 문장 앞뒤에 무음을 얹어서
    # 내보내는데, 그걸 정답 구간에 포함시키면 "무음을 잘라냈다"는 정상 동작이
    # 과잉컷으로 채점된다. 정답은 실제 발화 구간이어야 한다.
    # (areverse 를 낀 건 앞쪽만 지우는 필터로 뒤쪽도 처리하려는 것 — 문장
    #  내부의 쉼표 쉼은 건드리지 않는다.)
    trim = ("silenceremove=start_periods=1:start_silence=0:start_threshold=-45dB,"
            "areverse,"
            "silenceremove=start_periods=1:start_silence=0:start_threshold=-45dB,"
            "areverse")
    norm: list[Path] = []
    for w in wavs:
        n = w.with_name(w.stem + "_n.wav")
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                        "-i", str(w), "-af", trim, "-ar", str(sr), "-ac", "1",
                        "-c:a", "pcm_s16le", str(n)], check=True)
        norm.append(n)
    wavs = norm

    silence = tmp / "gap.wav"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", f"anullsrc=r={sr}:cl=mono",
                    "-t", str(GAP_S), "-c:a", "pcm_s16le", str(silence)], check=True)

    concat, truth, t = [], [], 0.0
    concat.append(silence)          # 맨 앞에도 여유
    t += GAP_S
    for (text, label), w in zip(SCRIPT, wavs):
        d = wav_dur(w)
        truth.append({"start": round(t, 3), "end": round(t + d, 3),
                      "text": text, "label": label})
        concat.append(w)
        t += d
        concat.append(silence)
        t += GAP_S

    listf = tmp / "list.txt"
    listf.write_text(
        "\n".join(f"file '{p.as_posix()}'" for p in concat), encoding="utf-8")

    audio = tmp / "voice.wav"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "concat", "-safe", "0", "-i", str(listf),
                    "-ar", str(sr), "-ac", "1", str(audio)], check=True)

    total = t
    out = OUT_DIR / "test_korean.mp4"
    font = "C\\:/Windows/Fonts/arialbd.ttf"
    vf = (f"drawtext=fontfile='{font}':text='%{{eif\\:t\\:d\\:2}}.%{{eif\\:mod(t*10,10)\\:d\\:1}}s'"
          f":fontsize=110:fontcolor=white:x=(w-tw)/2:y=(h-th)/2-120")

    print(f"영상 합성 중… ({total:.1f}s)")
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"color=c=0x0a0a0a:s=720x1280:r=30:d={total:.3f}",
        "-i", str(audio),
        "-vf", vf, "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-shortest", str(out),
    ], check=True)

    truth_path = OUT_DIR / "test_korean.truth.json"
    truth_path.write_text(json.dumps(
        {"gap_s": GAP_S, "duration_s": round(total, 3), "lines": truth},
        ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\n생성 완료")
    print(f"  영상   {out}")
    print(f"  정답   {truth_path}")
    print(f"  길이   {total:.2f}s / 대사 {len(SCRIPT)}줄 "
          f"(keep {sum(1 for _, l in SCRIPT if l == 'keep')} · "
          f"filler {sum(1 for _, l in SCRIPT if l == 'filler')} · "
          f"ng {sum(1 for _, l in SCRIPT if l == 'ng')})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
