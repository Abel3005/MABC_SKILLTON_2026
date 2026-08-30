"""캘린더 창.

`GOOGLECALENDAR_FIND_FREE_SLOTS`가 준 busy 구간만으로 지금 쓸 수 있는 시간을 계산한다.

**일정 제목을 받지 않는다.** 필요한 것은 셋뿐이고 셋 다 구간만으로 나온다 —
다음 일정까지 남은 시간, 직전 일정이 방금 끝났는지, 카드 크기 상한.
제목을 받지 않으므로 무슨 일정인지 시스템도 모른다. 그래서 카드에 "회의"라고
쓰지 않고 "다음 일정"이라고만 쓴다.

스크립트는 네트워크를 쓰지 않는다. 커넥터 호출은 모델이 하고 결과만 여기로 넘어온다.
"""

import re
from datetime import datetime, timedelta, timezone

# 직전 일정이 이 시간 안에 끝났으면 "방금 끝남"으로 본다.
RECENT_MINUTES = 10

# 남은 시간이 이보다 짧으면 카드 크기에 상한이 걸린다.
_MINIMAL_UNDER = 15
_SMALL_UNDER = 45

# 캐시 수명. 이보다 오래된 스냅샷은 쓰지 않는다.
TTL_MINUTES = 15

_SEP_RE = re.compile(r"\s*[~/]\s*")
_HHMM_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


def parse_time(text, reference=None):
    """ISO 8601 또는 `HH:MM`을 datetime으로. 실패하면 None."""
    if not text:
        return None
    text = text.strip()

    match = _HHMM_RE.match(text)
    if match:
        if reference is None:
            reference = datetime.now()
        return reference.replace(
            hour=int(match.group(1)), minute=int(match.group(2)),
            second=0, microsecond=0,
        )

    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def parse_busy(specs, reference=None, offset=None):
    """`"11:00~11:40"` 또는 ISO 쌍의 리스트를 (시작, 끝) 목록으로.

    결과는 전부 `offset` 기준 벽시계 값이다. 파싱되지 않는 항목은 조용히 버린다 —
    캘린더 때문에 카드가 안 나가면 안 된다.
    """
    out = []
    for spec in specs or []:
        parts = _SEP_RE.split(str(spec).strip())
        if len(parts) != 2:
            continue
        start = parse_time(parts[0], reference)
        end = parse_time(parts[1], reference)
        if start is None or end is None:
            continue
        start, end = naive(start, offset), naive(end, offset)
        if end <= start:
            continue
        out.append((start, end))
    return sorted(out)


def naive(dt, offset=None):
    """타임존 유무가 섞여도 비교할 수 있게 벽시계 값만 남긴다.

    **그냥 떼면 안 된다.** 커넥터는 `2026-08-30T06:20:00Z`처럼 UTC로 주는 경우가
    있고, tzinfo만 떼면 06시 20분이 되어 KST 15시 20분과 9시간 어긋난다.
    `offset`이 주어지면 그 시간대로 변환한 뒤 뗀다.

    시간대 이름(`Asia/Seoul`)이 아니라 오프셋(`+09:00`)을 쓴다. zoneinfo는 윈도우에서
    tzdata 패키지를 요구하는데 이 스킬은 표준 라이브러리만 쓴다.
    """
    if dt.tzinfo is None:
        return dt
    if offset is not None:
        return dt.astimezone(timezone(offset)).replace(tzinfo=None)
    return dt.replace(tzinfo=None)


def analyze(busy, now):
    """busy 구간과 현재 시각으로 창을 계산한다.

    Returns:
        {"next_start": "HH:MM"|None, "minutes_free": int|None,
         "just_finished": int|None, "ceiling": str|None, "in_event": bool}
    """
    now_n = naive(now)
    next_start = None
    last_end = None
    in_event = False

    for start, end in busy:
        s, e = naive(start), naive(end)
        if s <= now_n < e:
            in_event = True
        if s > now_n and (next_start is None or s < next_start):
            next_start = s
        if e <= now_n and (last_end is None or e > last_end):
            last_end = e

    if in_event:
        minutes_free = 0
    elif next_start is not None:
        minutes_free = max(0, int((next_start - now_n).total_seconds() // 60))
    else:
        minutes_free = None

    just_finished = None
    if last_end is not None and not in_event:
        elapsed = int((now_n - last_end).total_seconds() // 60)
        if 0 <= elapsed <= RECENT_MINUTES:
            just_finished = elapsed

    return {
        "next_start": next_start.strftime("%H:%M") if next_start else None,
        "minutes_free": minutes_free,
        "just_finished": just_finished,
        "ceiling": ceiling_for(minutes_free),
        "in_event": in_event,
    }


def ceiling_for(minutes_free):
    """남은 시간이 정하는 카드 크기 상한. 제약이 없으면 None."""
    if minutes_free is None:
        return None
    if minutes_free < _MINIMAL_UNDER:
        return "minimal"
    if minutes_free < _SMALL_UNDER:
        return "small"
    return None


def empty_window():
    """캘린더 데이터가 없을 때. 스킬은 이 상태로도 완전히 동작한다."""
    return {
        "next_start": None,
        "minutes_free": None,
        "just_finished": None,
        "ceiling": None,
        "in_event": False,
        "source": "none",
    }


def apply_mode(mode, window):
    """창이 모드를 덮어쓴다.

    직전 일정이 방금 끝났으면 시각과 무관하게 `after_event`다. README의 목표 타깃
    "회의 혹은 짧은 쉬는 시간을 마친 회사원"은 시각만으로는 판정할 수 없고
    캘린더가 있어야 잡힌다.

    무슨 일정이었는지는 모르므로 `after_meeting`이 아니라 `after_event`다.
    """
    adjusted = dict(mode)
    if window.get("just_finished") is not None:
        adjusted["mode"] = "after_event"
        adjusted["default_size"] = _shrink(adjusted.get("default_size", "medium"))
    return adjusted


_SIZE_ORDER = ["large", "medium", "small", "minimal"]


def _shrink(size):
    try:
        idx = _SIZE_ORDER.index(size)
    except ValueError:
        return size
    return _SIZE_ORDER[min(idx + 1, len(_SIZE_ORDER) - 1)]
