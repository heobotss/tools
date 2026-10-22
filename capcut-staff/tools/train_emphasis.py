"""강조 색 판별기를 대표님 완성본으로 학습한다.

    python tools/train_emphasis.py                 # 등록된 완성본 전부로 학습·저장
    python tools/train_emphasis.py 풀링5 풀링6 ...  # 새 완성본을 목록에 등록하고 다시 학습

완성본 목록은 capcut_agent/templates/finished_drafts.json 에 쌓인다 (대표님과 계속 학습 — 2026-09-17).

왜 학습인가 (2026-09-17 실측): 낱말 사전(style.score)만으로는 풀링4 136문장 중
18문장에만 걸려 칠한 양이 13% 에 그쳤다 (대표님 25%). 대표님이 칠한 줄/안 칠한 줄로
글자 조각 판별기를 학습해 사전 점수와 더하면, 처음 보는 편 기준으로
칠한 양 26% · 줄 일치 F1 48.5 · 빨/파 83% (사전만: 18% · 46.8 · 90%).

참고: 사람이 글을 읽고 골라도 대표님 선택과 줄 단위 F1 40 이었다. 강조는 정답이
하나가 아니다 — 양·길이·빨/파·뜻이 맞는 게 목표다.

저장: capcut_agent/templates/emphasis_model.pkl
"""
from __future__ import annotations

import pickle
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from compare_finish import Draft  # noqa: E402

OUT = HERE.parent / "capcut_agent" / "templates" / "emphasis_model.pkl"
REGISTRY = HERE.parent / "capcut_agent" / "templates" / "finished_drafts.json"
DEFAULT = ["풀링4", "풀링3", "키컨텐츠 2 완성", "0818_풀링_비트14",
           ".cloud_cache_7317641888214303745/0811"]
END = re.compile(r"([.?!]|(요|다|죠|까|냐|네|니다))\s*$")


def sentences(name: str):
    D = Draft(name)
    rows = [(x[2].strip(), x[3]) for x in D.subs if x[2].strip()]
    out, cur = [], []
    for t, k in rows:
        cur.append((t, k))
        if END.search(t):
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def main() -> int:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression

    import json
    names = (json.loads(REGISTRY.read_text(encoding="utf-8")) if REGISTRY.exists() else list(DEFAULT))
    for n in sys.argv[1:]:
        if n not in names:
            names.append(n)
    REGISTRY.write_text(json.dumps(names, ensure_ascii=False, indent=1), encoding="utf-8")
    T, Y, K, KY = [], [], [], []
    for n in names:
        try:
            ss = sentences(n)
        except Exception as e:                      # noqa: BLE001
            print(f"  건너뜀 {n}: {e}")
            continue
        for s in ss:
            text = " ".join(t for t, _ in s)
            ks = [k for _, k in s if k != "normal"]
            T.append(text)
            Y.append(bool(ks))
            if ks:
                K.append(text)
                KY.append(max(set(ks), key=ks.count) == "neg")
        print(f"  {n}: 문장 {len(ss)}")
    vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(1, 3), min_df=2,
                          sublinear_tf=True).fit(T + K)
    m = LogisticRegression(C=2, max_iter=3000, class_weight="balanced").fit(vec.transform(T), Y)
    mk = LogisticRegression(C=2, max_iter=3000).fit(vec.transform(K), KY)
    OUT.write_bytes(pickle.dumps({"vec": vec, "pick": m, "kind": mk, "videos": names}))
    print(f"저장 {OUT} · 문장 {len(T)} (색 {sum(Y)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
