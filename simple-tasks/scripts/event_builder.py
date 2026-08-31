"""일정 등록 검문과 `CREATE_EVENT` 인자 조립.

**받아쓰기만 한다.** 사용자가 말한 시각을 캘린더에 옮겨 적을 뿐, 언제 할지를
정해 주지 않는다. 그것은 계획 수립이고 절대 규칙 3번이 금지한다. 둘을 가르는
선은 하나다 — **발화에 시간 표현이 있는가.** 없으면 만들지 않고 되묻는다.

검문은 `utterance_guard`를 그대로 쓴다. 마감을 지어내지 못하게 막던 장치가
일정을 지어내지 못하게 막는 데 그대로 맞는다. 가짜 마감은 상태 파일을 오염시키고
가짜 일정은 사용자의 실제 캘린더를 오염시키므로, 이쪽이 더 엄하다.

**한국어 시간 표현을 파싱하지 않는다.** "내일 3시" → datetime 변환은 모델이 한다.
스크립트가 네트워크를 쓰지 않는 것과 같은 이유다. 여기서 하는 일은 모델이 뽑아 온
값이 발화에 근거가 있는지 검사하는 것뿐이다.
"""

from datetime import datetime, timedelta

from scripts import utterance_guard

# `--end`가 없을 때의 기본 길이. **이것은 지어내는 값이다.**
# 그래서 확인 문구에 반드시 드러나고(`15:00~16:00`), 응답에 `end_defaulted`로 표시된다.
# 사용자가 확인 단계에서 보고 거부할 수 있으므로 조용한 날조가 되지 않는다.
DEFAULT_DURATION_MINUTES = 60

_WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]


def parse_iso(text):
    """오프셋이 붙은 ISO 8601만 받는다. 아니면 None.

    **오프셋을 필수로 하는 이유.** 오프셋이 없으면 커넥터가 서버 시간대로 해석하고,
    캘린더에 9시간 어긋난 일정이 남는다. 읽기에서는 카드가 한 장 이상해질 뿐이지만
    쓰기에서는 사용자의 캘린더에 영구히 남는다.
    """
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(str(text).strip())
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def vet(utterance, title, start_text, end_text=None, now_text=None):
    """등록하려는 일정이 발화에 근거가 있는지 검사한다.

    Returns:
        통과: {"ok": True, "title":..., "start":datetime, "end":datetime,
               "end_defaulted": bool, "in_past": bool, "report": {...}}
        거부: {"ok": False, "refused": <사유>, "ask": <사용자에게 할 말>}
    """
    if not utterance:
        return _refuse("no_utterance",
                       "발화 원문 없이는 일정을 만들지 않는다. --utterance 를 붙인다.")

    # 이 검사가 받아쓰기와 계획 수립을 가른다. 통과하지 못하면 시각을 정해 주지 않는다.
    if not utterance_guard.has_time_expression(utterance):
        return _refuse("no_time_in_utterance", "몇 시로 넣을까?")

    if not title or not str(title).strip():
        return _refuse("no_title", "일정 이름을 뭐라고 할까?")

    start = parse_iso(start_text)
    if start is None:
        return _refuse("bad_start",
                       "시작 시각을 오프셋 붙인 ISO로 넘긴다. 예: 2026-08-31T15:00:00+09:00")

    end_defaulted = False
    if end_text:
        end = parse_iso(end_text)
        if end is None:
            return _refuse("bad_end",
                           "종료 시각을 오프셋 붙인 ISO로 넘긴다. 예: 2026-08-31T16:00:00+09:00")
    else:
        end = start + timedelta(minutes=DEFAULT_DURATION_MINUTES)
        end_defaulted = True

    if end <= start:
        return _refuse("end_before_start", "종료가 시작보다 빠르다. 시각을 다시 확인한다.")

    # 제목은 발화에 근거가 있어야 한다. 근거 없는 어절은 잘라내고, 전부 잘려 나가면
    # 만들지 않는다 — 캘린더에 남는 제목을 지어내는 것은 항목 이름을 지어내는 것보다 나쁘다.
    inspected = utterance_guard.inspect(str(title).strip(), utterance, strict=True)
    clean_title = (inspected.get("name") or "").strip()
    if not clean_title:
        return _refuse("title_unsupported", "일정 이름을 뭐라고 할까?")

    still_missing = utterance_guard.unsupported_tokens(clean_title, utterance)
    if still_missing and len(still_missing) == len(clean_title.split()):
        return _refuse("title_unsupported", "일정 이름을 뭐라고 할까?")

    in_past = False
    now = parse_iso(now_text)
    if now is not None:
        in_past = start < now

    return {
        "ok": True,
        "title": clean_title,
        "start": start,
        "end": end,
        "end_defaulted": end_defaulted,
        "in_past": in_past,
        "report": inspected.get("report") or {},
    }


def _refuse(reason, ask):
    return {"ok": False, "refused": reason, "ask": ask}


def confirm_text(title, start, end, end_defaulted=False, in_past=False, overlaps=False):
    """사용자에게 보여 줄 확인 문구.

    **스크립트가 만든다.** 모델이 쓰면 검문을 통과한 값과 화면에 뜨는 값이 달라질 수
    있고, 그러면 사용자는 자기가 승인하지 않은 일정을 승인하게 된다.
    """
    day = f"{start.month}/{start.day}({_WEEKDAYS[start.weekday()]})"
    span = f"{start:%H:%M}~{end:%H:%M}"
    line = f"{day} {span} · {title}"

    notes = []
    if end_defaulted:
        notes.append(f"길이 {DEFAULT_DURATION_MINUTES}분은 기본값")
    if overlaps:
        notes.append("그 시간에 다른 일정 있음")
    if in_past:
        notes.append("지난 시각")
    if notes:
        line += "  (" + ", ".join(notes) + ")"
    return line


def build_fields(title, start, end, calendar_id="primary"):
    """커넥터에 넘길 의미 단위 값. 키 이름이 아니라 값이 여기서 확정된다.

    스키마가 런타임마다 다를 수 있어 키 이름은 바뀔 수 있지만 **값은 바뀌면 안 된다.**
    검문을 통과한 것은 이 값들이기 때문이다. 그래서 조립된 args와 별도로 이것을
    함께 돌려주고, 스키마가 다르면 키만 옮기라고 지시한다.
    """
    return {
        "summary": title,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "calendar_id": calendar_id,
    }


def build_args(fields):
    """`composio_execute`의 `args`로 넘길 JSON 문자열.

    Google Calendar API의 events.insert 형태를 따른다. 커넥터가 다른 이름을 쓰면
    실행이 실패하고, 그때 `fields`의 값을 실제 스키마로 옮긴다.
    """
    import json

    payload = {
        "calendar_id": fields["calendar_id"],
        "summary": fields["summary"],
        "start_datetime": fields["start"],
        "end_datetime": fields["end"],
    }
    return json.dumps(payload, ensure_ascii=False)


def overlaps_busy(start, end, busy_pairs):
    """등록하려는 구간이 기존 busy 구간과 겹치는가.

    겹쳐도 막지 않는다 — 겹치는 일정을 일부러 넣는 경우가 있다. 확인 문구에만 띄운다.
    """
    for b_start, b_end in busy_pairs or []:
        if start < b_end and b_start < end:
            return True
    return False
