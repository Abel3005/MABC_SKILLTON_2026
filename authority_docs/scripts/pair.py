#!/usr/bin/env python3
"""주장 목록에서 충돌 후보 쌍을 추린다.

전수 비교는 주장 수의 제곱이라 감당이 안 된다. 여기서 값싼 규칙과 문자 n-gram
유사도로 후보를 좁히고, 실제 판정은 Claude 가 이 결과물만 읽고 수행한다.

사용법:
    python3 scripts/pair.py <작업폴더> [--top-k 40] [--min-sim 0.25] [--changed D003]

입력:  <작업폴더>/claims.jsonl
출력:  <작업폴더>/pairs.jsonl,  <작업폴더>/pair_stats.json

--changed 를 주면 해당 문서(개정안)와 관련된 쌍만 남긴다. 개정 영향 분석 모드.
"""
import argparse
import json
from collections import Counter
from itertools import combinations
from pathlib import Path

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


def load_claims(work: Path):
    claims = []
    with (work / "claims.jsonl").open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                claims.append(json.loads(line))
    return claims


def scope_overlap(a, b) -> bool:
    """적용범위가 겹치는지. 한쪽이라도 비어 있으면 겹치는 것으로 본다(보수적)."""
    sa, sb = a.get("scope") or {}, b.get("scope") or {}
    for key in set(sa) | set(sb):
        va, vb = sa.get(key), sb.get(key)
        if not va or not vb:
            continue
        va = {str(x) for x in (va if isinstance(va, list) else [va])}
        vb = {str(x) for x in (vb if isinstance(vb, list) else [vb])}
        if "전사" in va or "전직원" in va or "전체" in va:
            continue
        if "전사" in vb or "전직원" in vb or "전체" in vb:
            continue
        if not (va & vb):
            return False
    return True


def hints(a, b, sim):
    """판정 전 단서. Claude 가 우선순위를 잡는 데 쓴다."""
    out = []
    ta, tb = a.get("tier"), b.get("tier")
    if ta and tb and ta != tb:
        out.append("위계상이")
    if a.get("doc_id") == b.get("doc_id"):
        out.append("동일문서내")
    ea, eb = a.get("effective_from"), b.get("effective_from")
    if ea and eb and ea[:4] != eb[:4]:
        out.append("시행일차이")
    if a.get("subject") and a.get("subject") == b.get("subject"):
        out.append("주제일치")
    if a.get("modality") == "정의" and b.get("modality") == "정의":
        out.append("정의대정의")
    va, vb = a.get("value"), b.get("value")
    if va and vb and str(va).strip() != str(vb).strip():
        out.append("값불일치")
    if sim >= 0.55:
        out.append("고유사도")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("work")
    ap.add_argument("--top-k", type=int, default=40, help="주장 하나당 남길 최대 후보 수")
    ap.add_argument("--min-sim", type=float, default=0.25)
    ap.add_argument("--changed", default=None, help="개정 문서 doc_id (쉼표 구분)")
    ap.add_argument("--max-pairs", type=int, default=1500)
    ap.add_argument("--skip-judged", action="store_true",
                    help="verdicts.jsonl 에 이미 판정된 쌍은 제외한다")
    args = ap.parse_args()

    work = Path(args.work)
    claims = load_claims(work)
    if len(claims) < 2:
        print("주장이 2건 미만이라 비교할 것이 없습니다.")
        return

    texts = [f"{c.get('subject', '')} {c.get('text', '')}" for c in claims]
    vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), min_df=1, sublinear_tf=True)
    mat = vec.fit_transform(texts)
    sim = cosine_similarity(mat)

    changed = set((args.changed or "").split(",")) - {""}
    judged = set()
    if args.skip_judged and (work / "verdicts.jsonl").exists():
        for line in (work / "verdicts.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                v = json.loads(line)
                if v.get("key"):
                    judged.add(v["key"])
        print(f"기판정 {len(judged)}쌍 제외")
    idx_by_subject = {}
    for i, c in enumerate(claims):
        s = (c.get("subject") or "").strip()
        if s:
            idx_by_subject.setdefault(s, []).append(i)

    cand = set()
    # 1) 주제가 같으면 유사도와 무관하게 후보
    for ids in idx_by_subject.values():
        if 2 <= len(ids) <= 30:
            cand.update(combinations(sorted(ids), 2))
    # 2) 유사도 상위 K
    n = len(claims)
    for i in range(n):
        order = sim[i].argsort()[::-1]
        kept = 0
        for j in order:
            if j == i or sim[i][j] < args.min_sim:
                continue
            cand.add((min(i, j), max(i, j)))
            kept += 1
            if kept >= args.top_k:
                break

    rows = []
    for i, j in cand:
        a, b = claims[i], claims[j]
        if changed and not (a["doc_id"] in changed or b["doc_id"] in changed):
            continue
        if not scope_overlap(a, b):
            continue
        if judged and "|".join(sorted([a["claim_id"], b["claim_id"]])) in judged:
            continue
        s = float(sim[i][j])
        h = hints(a, b, s)
        score = s + 0.15 * len(h)
        rows.append(
            {
                "pair_id": f"P{len(rows) + 1:04d}",
                "key": "|".join(sorted([a["claim_id"], b["claim_id"]])),
                "score": round(score, 3),
                "similarity": round(s, 3),
                "hints": h,
                "a": {k: a.get(k) for k in ("claim_id", "doc_id", "filename", "locator",
                                            "subject", "text", "tier", "effective_from",
                                            "modality", "value", "scope")},
                "b": {k: b.get(k) for k in ("claim_id", "doc_id", "filename", "locator",
                                            "subject", "text", "tier", "effective_from",
                                            "modality", "value", "scope")},
            }
        )

    rows.sort(key=lambda r: -r["score"])
    rows = rows[: args.max_pairs]
    for k, r in enumerate(rows, 1):
        r["pair_id"] = f"P{k:04d}"

    with (work / "pairs.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    hc = Counter(h for r in rows for h in r["hints"])
    stats = {
        "claims": len(claims),
        "possible_pairs": n * (n - 1) // 2,
        "candidates": len(rows),
        "reduction": f"{100 * (1 - len(rows) / max(1, n * (n - 1) // 2)):.1f}%",
        "hint_counts": dict(hc),
        "mode": "개정영향분석" if changed else "전체감사",
    }
    (work / "pair_stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    print(f"→ {work}/pairs.jsonl")


if __name__ == "__main__":
    main()