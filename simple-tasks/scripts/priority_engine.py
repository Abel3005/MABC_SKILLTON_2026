"""우선순위 엔진.

열린 항목 목록에서 다음에 낼 카드를 선택한다.
첫 카드와 이후 카드의 선택 기준이 다르다.
"""

import random
import re
from datetime import datetime


SIZE_SCORE = {"minimal": 4, "small": 3, "medium": 2, "large": 1}
SIZE_ALIASES = {"S": "small", "M": "medium", "L": "large"}


def _normalize_size(size: str) -> str:
    return SIZE_ALIASES.get(size, size.lower())


def _is_deadline_today(deadline: str | None) -> bool:
    if not deadline:
        return False
    today = datetime.now().strftime("%m-%d")
    return deadline.startswith(today)


def _is_deadline_within_24h(deadline: str | None) -> bool:
    if not deadline:
        return False
    now = datetime.now()
    # "08-28 18:00" 형태 파싱 시도
    match = re.match(r"(\d{2})-(\d{2})\s+(\d{2}):(\d{2})", deadline)
    if match:
        month, day, hour, minute = (int(x) for x in match.groups())
        try:
            target = now.replace(month=month, day=day, hour=hour, minute=minute, second=0)
            diff = (target - now).total_seconds()
            return 0 < diff <= 86400
        except ValueError:
            return False
    # "08-28" 형태 (시간 없음) → 그 날 자정까지
    match = re.match(r"(\d{2})-(\d{2})$", deadline)
    if match:
        month, day = int(match.group(1)), int(match.group(2))
        try:
            target = now.replace(month=month, day=day, hour=23, minute=59, second=59)
            diff = (target - now).total_seconds()
            return 0 < diff <= 86400
        except ValueError:
            return False
    return False


def _filter_candidates(open_items, calibration, just_harvested):
    """후보 필터링: muted, needs_prep, just_harvested 제외."""
    candidates = []
    for item in open_items:
        name = item.get("name", "")

        # just_harvested 제외
        if name in (just_harvested or []):
            continue

        # needs_prep 제외 (calibration에서 온 정보 반영)
        if item.get("needs_prep"):
            continue
        if name in (calibration or {}).get("items_needing_prep", []):
            continue

        # muted 제외 (예외: 24시간 내 마감)
        if item.get("muted"):
            if not _is_deadline_within_24h(item.get("deadline")):
                continue

        candidates.append(item)

    # 후보가 0개면 muted 항목도 포함
    if not candidates:
        for item in open_items:
            name = item.get("name", "")
            if name in (just_harvested or []):
                continue
            if item.get("needs_prep"):
                continue
            if name in (calibration or {}).get("items_needing_prep", []):
                continue
            candidates.append(item)

    return candidates


def _score_first_card(item: dict) -> float:
    """첫 카드 점수: 완료 가능성 > 생각 유발 > 정확도."""
    score = 0.0

    # 완료 가능성: 크기 작을수록 높은 점수
    size = _normalize_size(item.get("size", "medium"))
    score += SIZE_SCORE.get(size, 2) * 10

    # 생각 유발: 재진입 메모가 있으면 가점
    if item.get("reentry"):
        score += 5

    # 정확도: 마감 가까우면 가점
    if _is_deadline_today(item.get("deadline")):
        score += 3

    return score


def _blocking_count(item: dict) -> int:
    """이 항목이 막고 있는 것의 수.

    `blocks`에 이름이 들어 있으면 그것을 세고, 없으면 예전 `blocking` 숫자를 쓴다.
    이름이 있어야 `이유` 줄에서 무엇이 풀리는지 부를 수 있다.
    """
    named = item.get("blocks") or []
    if named:
        return len(named)
    try:
        return int(item.get("blocking") or 0)
    except (TypeError, ValueError):
        return 0


def _warm_context(item: dict, last_context: list | None) -> list:
    """직전 완료 항목과 공유하는 맥락. 순서를 유지한 교집합."""
    if not last_context:
        return []
    shared = set(last_context)
    return [c for c in (item.get("context") or []) if c in shared]


def _score_subsequent_card(item: dict, last_context: list | None = None) -> float:
    """이후 카드 점수."""
    score = 0.0

    # 재진입 메모 있는 항목 — 최우선
    if item.get("reentry"):
        score += 100

    # 마감이 오늘
    if _is_deadline_today(item.get("deadline")):
        score += 50

    # 직전 완료와 맥락을 공유 — 파일이 이미 열려 있고 사람 이름이 머리에 있다.
    # 셋업 비용이 이미 지불되어 실제로 행동 비용이 낮다.
    if _warm_context(item, last_context):
        score += 40

    # 차단중 수 큰 것
    score += _blocking_count(item) * 10

    # 크기 작은 것
    size = _normalize_size(item.get("size", "medium"))
    score += SIZE_SCORE.get(size, 2)

    return score


def pick(open_items: list, mode: dict, calibration: dict | None = None,
         is_first_card: bool = True, just_harvested: list | None = None,
         last_context: list | None = None) -> dict | None:
    """다음에 낼 항목을 선택.

    Args:
        open_items: 열린 항목 리스트
        mode: mode_detector.detect() 결과
        calibration: size_calibrator.calibrate() 결과 (optional)
        is_first_card: 첫 카드 여부
        just_harvested: 방금 수확한 항목 이름 리스트
        last_context: 직전 완료 항목의 맥락 (따뜻한 맥락 가점용)

    Returns:
        {"name": str, "reason": str, "why": dict, "score_detail": dict} or None
    """
    if not open_items:
        return None

    candidates = _filter_candidates(open_items, calibration, just_harvested)

    if not candidates:
        return None

    scored = []
    for item in candidates:
        if is_first_card:
            score = _score_first_card(item)
        else:
            score = _score_subsequent_card(item, last_context)
        scored.append((score, item))

    # 최고 점수 찾기
    max_score = max(s for s, _ in scored)
    top = [(s, item) for s, item in scored if s == max_score]

    # 동점이면 랜덤
    _, picked = random.choice(top)

    return {
        "name": picked["name"],
        "reason": "picked",
        # `이유` 줄에 쓸 사실. 그래프가 사용자 화면에 드러나는 유일한 지점이다.
        # 비어 있으면 모델이 마감·시각으로 쓴다.
        "why": {
            "unblocks": list(picked.get("blocks") or []),
            "warm": _warm_context(picked, last_context),
            "spawned_by": picked.get("spawned_by"),
        },
        "score_detail": {
            "score": max_score,
            "is_first_card": is_first_card,
            "mode": mode.get("mode", ""),
            "candidates_count": len(candidates),
        },
    }
