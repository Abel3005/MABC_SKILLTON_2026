"""Simple Tasks CLI 진입점.

모든 출력은 JSON. argparse 서브커맨드로 기능을 노출한다.
"""

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

# 스크립트를 직접 실행할 때 패키지 임포트가 가능하도록
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR.parent) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR.parent))

from scripts import state_manager
from scripts import mode_detector
from scripts import size_calibrator
from scripts import priority_engine
from scripts import session_controller
from scripts import harvest_processor
from scripts import token_manager
from scripts import utterance_guard
from scripts import item_matcher
from scripts import calendar_window
# from scripts import event_builder   # 결선 이후. 아래 "일정 등록" 주석 블록과 함께 푼다.


# 배포 표식. 모든 `start` 출력에 실린다.
#
# **왜 필요한가.** SKILL.md를 고쳐도 zip을 다시 묶지 않으면 런타임에는 옛 스킬이
# 남는다. 실제로 그 상태로 세 번의 트레이스를 원인 분석하는 데 썼고, 트레이스만
# 봐서는 배포본이 무엇인지 알 방법이 없었다. 출력에 한 줄 실으면 즉시 판별된다.
BUILD = "2026-08-30.calendar-gate"

# 캘린더 절차를 한 번 돌려준 뒤 이 시간 안에 다시 맨몸 `start`가 오면
# 카드를 그냥 낸다. 커넥터가 없는 런타임에서 무한 왕복이 되지 않게 하는 안전장치다.
_PROMPT_COOLDOWN_SECONDS = 600

# 맨몸 `start`에 돌려주는 절차.
#
# **산문이 아니라 도구 출력으로 옮긴 이유.** SKILL.md의 캘린더 절은 실측 트레이스
# 세 건에서 단 한 번도 실행되지 않았다. `load_tools` 호출이 0회였다. 캘린더가 실제로
# 불린 것은 사용자가 캘린더를 직접 물어본 세션뿐이었다. 모델은 턴 맨 앞에서 한 번 읽은
# 23KB 문서보다 눈앞의 도구 출력을 따른다. 그래서 지시를 출력에 싣는다.
_CALENDAR_STEPS = {
    "why": "캘린더를 아직 읽지 않았다. busy 구간이 있으면 카드의 크기와 이유가 달라진다.",
    "do": [
        'load_tools(categories=["store"])',
        'composio_execute(tool="GOOGLECALENDAR_FIND_FREE_SLOTS", '
        'args="{\\"time_min\\": \\"<오늘>T00:00:00<오프셋>\\", '
        '\\"time_max\\": \\"<오늘>T23:59:59<오프셋>\\", '
        '\\"items\\": [{\\"id\\": \\"primary\\"}]}")',
        'start --now "<지금+오프셋>" --busy "HH:MM~HH:MM" (busy 구간마다 하나씩)',
    ],
    "do_not": [
        "LIST_CALENDARS, EVENTS_LIST, ACL_LIST 를 먼저 부르지 않는다",
        "composio_search_tools 로 먼저 찾지 않는다. 실패했을 때만 내려간다",
        "사용자에게 허락을 묻지 않는다",
    ],
    "if_it_fails": 'start --no-calendar 로 다시 부른다. 그러면 카드가 나온다. '
                   '커넥터를 여는 데 두 번 이상 쓰지 않는다.',
}


def _should_gate_on_calendar(state, args):
    """맨몸 `start`인가. 이미 한 번 절차를 돌려줬으면 더 막지 않는다."""
    if args.no_calendar or args.busy or args.now:
        return False

    prompted = state.get("calendar_prompted_at")
    if prompted:
        try:
            when = datetime.fromisoformat(prompted)
        except (TypeError, ValueError):
            when = None
        if when is not None:
            elapsed = (datetime.now() - when).total_seconds()
            if 0 <= elapsed < _PROMPT_COOLDOWN_SECONDS:
                return False
    return True


def _default_state_path():
    """상태 파일 경로.

    **스킬 패키지 안에 쓰지 않는다.** 스킬은 읽기 전용으로 설치되는 경우가 많고,
    그러면 저장이 실패해 카드가 통째로 죽는다. 실제로 그 사고가 났다 —
    stdout이 0바이트로 나가서 런타임에서는 "스크립트가 없다"로 보였다.
    """
    override = os.environ.get("SIMPLE_TASKS_STATE")
    if override:
        return override
    try:
        return str(Path.home() / ".simple-tasks" / "tasks.json")
    except (RuntimeError, OSError):
        return str(Path(tempfile.gettempdir()) / "simple-tasks" / "tasks.json")


def _output(data):
    """JSON 한 덩이를 stdout으로. **인코딩 때문에 죽지 않는다.**

    윈도우 콘솔은 cp949인 경우가 있고 `—`나 이모지가 하나만 섞여도 `print`가
    UnicodeEncodeError로 죽는다. 그러면 stdout이 비고 런타임에는 "스크립트가 없다"로
    보인다 — 이 스킬이 이미 한 번 그렇게 죽었다. 항목 이름은 사용자가 치는 것이므로
    좁은 콘솔에서 못 쓸 문자가 들어올 수 있다. 그때는 이스케이프해서라도 내보낸다.
    """
    text = json.dumps(data, ensure_ascii=False, indent=2)
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        text.encode(encoding)
    except (UnicodeEncodeError, LookupError):
        text = json.dumps(data, ensure_ascii=True, indent=2)
    print(text)


def _save(path, state, result):
    """상태를 저장한다. **실패해도 카드를 버리지 않는다.**

    이 스킬의 원칙 그대로다 — 상태가 한 세션 날아가는 것은 감수할 수 있는 손실이고
    사용자가 빈 화면 앞에 있는 것은 감수할 수 없는 손실이다. 저장 실패로 카드를
    죽이면 스크립트가 제 문서를 어기는 셈이 된다.
    """
    try:
        state_manager.save(path, state)
    except OSError as exc:
        result["state_saved"] = False
        result["state_error"] = f"{type(exc).__name__}: {exc}"


def _store_calendar(state, now_text, busy_specs, clear=False):
    """캘린더 스냅샷을 세션에 1회 저장한다.

    커넥터 호출은 네트워크라 느리다. 세션마다 한 번만 받아 두고 이후 카드는
    이 스냅샷으로 계산한다. busy 구간은 시간이 지나도 변하지 않으므로
    파생값(남은 시간 등)이 아니라 구간 자체를 캐시한다.
    """
    if clear:
        state["calendar_snapshot"] = None
        return
    if not busy_specs and not now_text:
        return

    local = datetime.now()
    provided = calendar_window.parse_time(now_text) if now_text else None

    # 사용자 시간대 오프셋. `--now`가 `+09:00`을 달고 오면 그것이 기준이 된다.
    # busy 구간이 UTC('Z')로 와도 이 오프셋으로 변환해야 9시간 어긋나지 않는다.
    offset_seconds = None
    if provided is not None and provided.tzinfo is not None:
        offset_seconds = provided.utcoffset().total_seconds()
    else:
        # `--now` 없이 `--busy`만 온 경우. 로컬 오프셋으로 둔다 — 구간이 `Z`로 오면
        # 기준이 없을 때 9시간 어긋나므로, 모르는 것보다 로컬 기준이 낫다.
        local_offset = local.astimezone().utcoffset()
        if local_offset is not None:
            offset_seconds = local_offset.total_seconds()

    # 커넥터가 준 벽시계와 로컬 시계의 차이. 서버 타임존이 사용자와 달라도
    # 모드 판정이 틀리지 않게 한다.
    delta = 0.0
    if provided is not None:
        wall = calendar_window.naive(
            provided,
            timedelta(seconds=offset_seconds) if offset_seconds is not None else None,
        )
        delta = (wall - local).total_seconds()

    state["calendar_snapshot"] = {
        "fetched_at": local.isoformat(),
        "clock_delta": delta,
        "utc_offset": offset_seconds,
        "busy": list(busy_specs or []),
    }


def _now_and_window(state):
    """유효 현재 시각과 캘린더 창.

    스냅샷이 없거나 오래됐으면 로컬 시각과 빈 창을 쓴다. **캘린더가 없어도
    스킬은 완전히 동작한다.** 커넥터는 있으면 좋은 것이지 필수가 아니다.
    """
    snap = state.get("calendar_snapshot")
    if not snap:
        return datetime.now(), calendar_window.empty_window()

    try:
        fetched = datetime.fromisoformat(snap["fetched_at"])
    except (KeyError, TypeError, ValueError):
        return datetime.now(), calendar_window.empty_window()

    if (datetime.now() - fetched).total_seconds() / 60 > calendar_window.TTL_MINUTES:
        return datetime.now(), calendar_window.empty_window()

    now = datetime.now() + timedelta(seconds=snap.get("clock_delta", 0))

    offset_seconds = snap.get("utc_offset")
    offset = timedelta(seconds=offset_seconds) if offset_seconds is not None else None
    busy = calendar_window.parse_busy(snap.get("busy") or [], now, offset)
    if not busy:
        window = calendar_window.empty_window()
        window["source"] = "calendar"
        return now, window

    window = calendar_window.analyze(busy, now)
    window["source"] = "calendar"
    return now, window


def _session_context(state):
    """현재 시각·모드·크기 보정·캘린더 창을 한 번에 계산한다."""
    now, window = _now_and_window(state)

    mode = calendar_window.apply_mode(
        mode_detector.detect(now.hour, now.minute), window,
    )

    calibration = size_calibrator.calibrate(
        state.get("rejection_log", []),
        mode["default_size"],
    )

    # 캘린더 상한과 거부 로그 보정 중 작은 쪽을 쓴다.
    tightened = size_calibrator.smaller_of(
        calibration["adjusted_size"], window.get("ceiling"),
    )
    if tightened != calibration["adjusted_size"]:
        calibration["adjusted_size"] = tightened
        calibration["shrunk"] = True
        calibration["limited_by_calendar"] = True

    return now, mode, calibration, window


def _situation(state, now, window):
    """잔여 상태 두 줄에 들어갈 값 **전부**. 항목 이름은 하나도 담기지 않는다.

    두 줄로 나눈 이유는 종류가 다르기 때문이다 — 첫 줄은 **일의 양**,
    둘째 줄은 **시간의 모양**이다. 한 줄에 여섯 개를 늘어놓으면 읽는 데 3초가 넘고,
    그러면 카드보다 요약을 읽는 시간이 길어진다.

    **첫 줄이 `미처리`로 시작하는 것은 절대 규칙 1 때문이다.** 시각을 앞에 두면
    응답의 첫 글자가 바뀐다. 시간 정보는 둘째 줄로 내린다.

    `clock`을 문자열로 미리 만들어 주는 것은 규칙 9(24시간제) 때문이다. 모델이
    "오후 3시"로 쓰면 아래 표의 행을 잘못 고른다. 계산해서 주면 틀릴 여지가 없다.
    """
    today = now.strftime("%m-%d")

    # `completed_at`은 `harvest_processor`가 `"08-30 11:55"`로 쓴다. 마감과 같은
    # `MM-DD` 접두사 비교를 쓴다 — `priority_engine._is_deadline_today`와 같은 방식이다.
    # ISO로 파싱하면 이 형식이 통째로 걸러져 완료가 0으로 나온다.
    completed_today = sum(
        1 for done in (state.get("completed") or [])
        if (done.get("completed_at") or "").startswith(today)
    )

    open_items = state.get("open_items") or []

    return {
        # 첫 줄 — 일의 양
        "open_count": len(open_items),
        "today_deadline_count": sum(
            1 for i in open_items if (i.get("deadline") or "").startswith(today)
        ),
        "completed_today": completed_today,
        # 둘째 줄 — 시간의 모양
        "clock": f"{now.hour}시 {now.minute}분",
        "minutes_free": window.get("minutes_free"),
        "next_start": window.get("next_start"),
        "events_left_today": window.get("remaining_today"),
    }


def _pick_with_token(state, is_first, just_harvested=None):
    """다음 항목을 고르고 토큰을 발급한다. state를 제자리에서 수정한다.

    호출자가 이후에 state_manager.save를 반드시 호출해야 한다.
    """
    now, mode, calibration, window = _session_context(state)

    result = priority_engine.pick(
        state.get("open_items", []),
        mode,
        calibration,
        is_first_card=is_first,
        just_harvested=just_harvested,
        last_context=state.get("last_context"),
    )

    if result is None:
        return {
            "name": None,
            "reason": "no_candidates",
            "mode": mode,
            "calibration": calibration,
            "window": window,
            "situation": _situation(state, now, window),
        }

    token = token_manager.generate()
    state["active_token"] = {
        "hash": token_manager.hash_token(token),
        "item": result["name"],
        "created_at": now.isoformat(),
    }

    result["mode"] = mode
    result["calibration"] = calibration
    result["window"] = window
    result["situation"] = _situation(state, now, window)
    result["token"] = token
    return result


def _token_for(state, item):
    """특정 항목에 토큰을 발급한다. `add --next`처럼 낼 카드가 이미 정해진 경우.

    사용자가 방금 이름을 말한 항목은 우선순위와 무관하게 그것이 카드가 되어야 한다.
    발화 자체가 가장 강한 신호이기 때문이다.
    """
    now, mode, calibration, window = _session_context(state)

    token = token_manager.generate()
    state["active_token"] = {
        "hash": token_manager.hash_token(token),
        "item": item["name"],
        "created_at": now.isoformat(),
    }

    return {
        "name": item["name"],
        "reason": "requested",
        "why": priority_engine.why_for(item, state.get("last_context")),
        "mode": mode,
        "calibration": calibration,
        "window": window,
        "situation": _situation(state, now, window),
        "token": token,
    }


def cmd_load(args):
    state = state_manager.load(args.state)
    session_info = session_controller.start_session(state)
    # 묵힘 해제 후 저장
    _save(args.state, state, session_info)
    _output(session_info)


def cmd_start(args):
    """load + pick --first 를 한 번에. 세션 시작용."""
    state = state_manager.load(args.state)

    # 캘린더는 세션당 1회만 받는다. 이후 카드는 이 스냅샷으로 계산하므로
    # 커넥터를 다시 부르지 않는다.
    _store_calendar(state, args.now, args.busy, clear=args.no_calendar)

    # 맨몸 `start`면 카드를 미루고 절차만 돌려준다. **카드를 함께 주면 안 된다** —
    # 절대 규칙 1이 "카드가 있으면 즉시 낸다"이므로, 카드가 응답에 들어 있는 한
    # 모델은 절차를 읽지 않고 그것을 낸다. 카드를 빼는 것이 지시를 읽게 만드는
    # 유일한 방법이다. 커넥터가 실패하면 `--no-calendar`로 곧바로 회수된다.
    if _should_gate_on_calendar(state, args):
        state["calendar_prompted_at"] = datetime.now().isoformat()
        gated = {
            "build": BUILD,
            "card": None,
            "reason": "calendar_first",
            "next_action": _CALENDAR_STEPS,
        }
        _save(args.state, state, gated)
        _output(gated)
        return

    now, mode, calibration, window = _session_context(state)

    session_info = session_controller.start_session(state, now=now)
    session_info["build"] = BUILD
    session_info["situation"] = _situation(state, now, window)
    session_info["mode"] = mode
    session_info["calibration"] = calibration
    session_info["window"] = window

    # 열린 카드가 있으면 이탈 회수가 먼저이므로 카드를 고르지 않는다
    if session_info["has_open_card"]:
        session_info["card"] = None
    else:
        session_info["card"] = _pick_with_token(state, is_first=True)

    _save(args.state, state, session_info)
    _output(session_info)


# ---------------------------------------------------------------------------
# 일정 등록(캘린더 쓰기) — **결선 이후 사용.** 공모전 제출 범위는 카드 흐름과
# 항목 그래프까지다. 되살리려면 아래 주석을 풀고, 29행의 event_builder import,
# schedule 서브파서, commands 의 "schedule" 항목을 함께 풀고,
# build_skill.py 의 DEFERRED 에 있는 scripts/event_builder.py 를 REQUIRED 로 옮긴다.
# 설계 근거는 references/design-notes.md 의 "일정 등록 턴" 절에 있다.
# ---------------------------------------------------------------------------
# def _event_overlaps(state, start, end):
#     """등록하려는 구간이 캐시된 busy 구간과 겹치는가.
#
#     스냅샷이 없으면 판정하지 않는다. 겹침은 막는 근거가 아니라 확인 문구에
#     띄우는 정보다 — 겹치는 일정을 일부러 넣는 경우가 있다.
#     """
#     snap = state.get("calendar_snapshot")
#     if not snap:
#         return False
#
#     offset_seconds = snap.get("utc_offset")
#     offset = timedelta(seconds=offset_seconds) if offset_seconds is not None else None
#     busy = calendar_window.parse_busy(snap.get("busy") or [], datetime.now(), offset)
#     if not busy:
#         return False
#
#     return event_builder.overlaps_busy(
#         calendar_window.naive(start, offset),
#         calendar_window.naive(end, offset),
#         busy,
#     )
#
#
# def _already_scheduled(state, fields):
#     """같은 제목·같은 시작 시각이 이미 기록돼 있는가."""
#     for entry in state.get("scheduled_events") or []:
#         if entry.get("summary") == fields["summary"] and entry.get("start") == fields["start"]:
#             return True
#     return False
#
#
# def cmd_schedule(args):
#     """사용자가 말한 일정을 캘린더에 등록할 준비를 한다.
#
#     **스크립트가 캘린더에 쓰지 않는다.** 검문하고, 확인 문구를 만들고, 커넥터에
#     넘길 인자를 조립해서 돌려줄 뿐이다. 실제 쓰기는 사용자가 승인한 뒤 모델이 한다.
#     """
#     state = state_manager.load(args.state)
#
#     if args.commit:
#         _commit_event(args, state)
#         return
#
#     vetted = event_builder.vet(args.utterance, args.title, args.start, args.end, args.now)
#     if not vetted["ok"]:
#         _output({"build": BUILD, **vetted})
#         return
#
#     fields = event_builder.build_fields(vetted["title"], vetted["start"], vetted["end"])
#
#     token = token_manager.generate()
#     state["pending_event"] = {
#         "hash": token_manager.hash_token(token),
#         "fields": fields,
#         "created_at": datetime.now().isoformat(),
#     }
#
#     result = {
#         "build": BUILD,
#         "ok": True,
#         "confirm": event_builder.confirm_text(
#             vetted["title"], vetted["start"], vetted["end"],
#             end_defaulted=vetted["end_defaulted"],
#             in_past=vetted["in_past"],
#             overlaps=_event_overlaps(state, vetted["start"], vetted["end"]),
#         ),
#         "already_scheduled": _already_scheduled(state, fields),
#         "must_confirm": "confirm 문구를 선택지 도구로 그대로 띄운다. "
#                         "사용자가 승인하기 전에는 실행하지 않는다.",
#         "execute": {
#             "tool": "GOOGLECALENDAR_CREATE_EVENT",
#             "args": event_builder.build_args(fields),
#             "fields": fields,
#             "if_rejected": "파라미터 이름이 다르면 composio_search_tools(toolkits=[\"googlecalendar\"], "
#                            "search=\"create event\", include_schemas=true)로 확인하고 fields의 "
#                            "값을 그 이름으로 옮긴다. **값은 바꾸지 않는다** — 검문을 통과한 것이 이 값들이다.",
#         },
#         "then": 'schedule --commit --token "<위 token>" --event-id "<응답의 id>"',
#         "token": token,
#     }
#     if vetted["report"]:
#         result["guard"] = vetted["report"]
#
#     _save(args.state, state, result)
#     _output(result)
#
#
# def _commit_event(args, state):
#     """등록이 끝난 일정을 기록한다. 중복 등록을 막고 주간 회고에 쓴다."""
#     pending = state.get("pending_event")
#     if not pending:
#         _output({"build": BUILD, "error": "no_pending_event",
#                  "message": "schedule 을 먼저 실행하세요"})
#         sys.exit(1)
#
#     if not args.token or not token_manager.validate(args.token, pending.get("hash")):
#         _output({"build": BUILD, "error": "invalid_token", "message": "잘못된 토큰입니다"})
#         sys.exit(1)
#
#     entry = dict(pending.get("fields") or {})
#     entry["event_id"] = args.event_id
#     entry["recorded_at"] = datetime.now().isoformat()
#
#     state.setdefault("scheduled_events", []).append(entry)
#     state["pending_event"] = None
#
#     result = {
#         "build": BUILD,
#         "ok": True,
#         "recorded": entry,
#         "scheduled_count": len(state["scheduled_events"]),
#     }
#     _save(args.state, state, result)
#     _output(result)
#
#

def cmd_pick(args):
    state = state_manager.load(args.state)
    result = _pick_with_token(state, is_first=args.first)
    _save(args.state, state, result)
    _output(result)


def _validate_token(state, provided_token, item_name):
    """토큰 검증. 실패 시 에러 dict와 exit code를 반환."""
    active = state.get("active_token")
    if not active:
        _output({"error": "no_active_token", "message": "pick을 먼저 실행하세요"})
        sys.exit(1)

    if not token_manager.validate(provided_token, active["hash"]):
        _output({"error": "invalid_token", "message": "잘못된 토큰입니다"})
        sys.exit(1)

    if active.get("item") != item_name:
        _output({"error": "item_mismatch", "message": "토큰과 항목이 일치하지 않습니다"})
        sys.exit(1)

    # 토큰 소모
    state["active_token"] = None


def cmd_complete(args):
    state = state_manager.load(args.state)

    # 토큰 검증
    _validate_token(state, args.token, args.item)

    # 열린 카드 닫기
    session_controller.close_card(state)

    result = harvest_processor.harvest(
        state,
        args.item,
        reentry_note=args.reentry,
        new_idea=args.new_idea,
    )
    state = result["state"]
    del result["state"]

    if args.next:
        # 방금 수확한 항목 제외는 harvest가 붙이는 `묵힘` 표시가 담당한다.
        # just_harvested를 여기서 넘기면 priority_engine의 예비 통과까지 막혀
        # "묵힘 아닌 항목이 하나도 없을 때는 수확한 것을 낸다"는 예외가 깨진다.
        result["card"] = _pick_with_token(state, is_first=False)

    _save(args.state, state, result)
    _output(result)


def cmd_reject(args):
    state = state_manager.load(args.state)

    # 토큰 검증
    _validate_token(state, args.token, args.item)

    # 열린 카드 닫기
    session_controller.close_card(state)

    result = session_controller.record_response(
        state, args.item, args.reason,
    )
    state = result["state"]
    del result["state"]

    if args.next:
        result["card"] = _pick_with_token(state, is_first=False)

    _save(args.state, state, result)
    _output(result)


def cmd_open_card(args):
    state = state_manager.load(args.state)
    result = session_controller.open_card(state, args.item)
    state_only = result.pop("state")
    _save(args.state, state_only, result)
    _output(result)


def cmd_close_card(args):
    state = state_manager.load(args.state)
    result = session_controller.close_card(state)
    state_only = result.pop("state")
    _save(args.state, state_only, result)
    _output(result)


def cmd_end_session(args):
    state = state_manager.load(args.state)
    result = session_controller.end_session(state)
    state_only = result.pop("state")
    _save(args.state, state_only, result)
    _output(result)


def cmd_add(args):
    state = state_manager.load(args.state)

    # 1. 검문 — 발화에 근거가 없는 값을 벗겨낸다. 거부하지 않는다.
    checked = utterance_guard.inspect(
        args.item,
        args.utterance,
        deadline=args.deadline,
        context=args.context or [],
        strict=args.strict_name,
    )

    result = {}
    if checked["report"]:
        result["guard"] = checked["report"]

    # 2. 매칭 — 이미 열려 있는 항목이면 새로 만들지 않는다.
    #
    # 매칭은 언제나 **발화에 근거가 남은 이름**으로 한다. `--strict-name`은 저장할
    # 이름만 정할 뿐 매칭에는 관여하지 않는다. 모델이 "3분기 보고서 작성"을 넘겨도
    # "보고서"로 매칭해야 기존 "3분기 보고서 초안"을 찾는다. 임계값을 낮춰서 풀면
    # 관계없는 항목까지 걸리므로, 이름을 다듬어서 푸는 편이 안전하다.
    match_name = checked["name"]
    if args.utterance:
        match_name = utterance_guard.trim_name(args.item, args.utterance)[0]

    matched = None
    if not args.no_match:
        matched = item_matcher.find_match(match_name, state.get("open_items", []))

    if matched:
        item, how, score = matched

        # 검문을 통과한 마감이 있고 기존 항목에 마감이 없으면 채운다.
        if checked["deadline"] and not item.get("deadline"):
            item["deadline"] = checked["deadline"]

        # 같은 항목을 다른 경로로 부른 것이므로 맥락은 합친다.
        merged = list(item.get("context") or [])
        for ctx in checked["context"]:
            if ctx not in merged:
                merged.append(ctx)
        item["context"] = merged

        result.update({
            "action": "matched",
            "matched_by": how,
            "score": score,
            "item": item,
        })
    else:
        item = {
            "name": checked["name"],
            "deadline": checked["deadline"],
            "size": args.size or "M",
            "blocking": args.blocking or 0,
            "reentry": args.reentry,
            "muted": None,
            "needs_prep": False,
            # 엣지 필드. 사용자가 발화한 것만 들어간다. 추론해서 채우지 않는다.
            "context": checked["context"],
            "blocks": args.blocks or [],
            "spawned_by": args.spawned_by,
            # 이 항목이 어느 발화에서 나왔는지. 나중에 값의 출처를 추적할 수 있다.
            "source_utterance": args.utterance,
        }
        state.setdefault("open_items", []).append(item)
        result.update({"action": "added", "item": item})

    result["open_items_count"] = len(state.get("open_items", []))

    # 사용자가 방금 이름을 말한 항목이므로 우선순위와 무관하게 이것이 카드가 된다.
    if args.next:
        result["card"] = _token_for(state, item)

    _save(args.state, state, result)
    _output(result)


def _demo_items(today):
    """시연용 항목.

    **회사 일만 있는 목록은 이 제품의 실제 사용 맥락이 아니다.** 사람의 하루에는
    보고서와 분리수거와 어머니 생신이 같은 목록에 섞여 있고, 뭘 먼저 할지 못 정하고
    맴도는 이유가 바로 그 이질성이다. 그래서 업무·집안·관계·건강을 섞었다.

    그래프 세 종류가 전부 한 번씩 드러나도록 짰다.

    - `context` 겹침 → 직전 완료 항목과 같은 것을 쓰는 항목에 따뜻한 맥락 가점
    - `blocks`      → "이거 끝나면 무엇이 풀리는지"를 `이유` 줄에 이름으로 쓸 수 있다
    - `spawned_by`  → 수확이 만든 엣지

    **실제 사용자 데이터가 아니다.** `seed`로만 들어오고, 발화에서 온 값이 아니므로
    `source_utterance`를 비워 둔다. 검문을 통과한 값과 구별되어야 한다.
    """
    return [
        {
            "name": "주간 업무 보고서 마무리", "deadline": today, "size": "L",
            "blocking": 0, "reentry": "이번 주 지표 표부터", "muted": None,
            "needs_prep": False,
            "context": ["주간보고.docx", "박팀장"],
            "blocks": ["팀 회의 발표 자료"], "spawned_by": None, "source_utterance": None,
        },
        {
            "name": "팀 회의 발표 자료", "deadline": None, "size": "M",
            "blocking": 0, "reentry": None, "muted": None, "needs_prep": True,
            "context": ["주간보고.docx"],
            "blocks": [], "spawned_by": None, "source_utterance": None,
        },
        # 이 항목이 첫 카드가 되도록 짰다. 작아서 캘린더 상한을 통과하고, 오늘 마감이
        # 있어 우선순위가 높고, 그래프 세 종류가 한 항목에 다 들어 있다 —
        # 직전에 완료한 "치과 진료비 영수증 찾기"에서 파생됐고(`spawned_by`),
        # 그 맥락을 물려받았고(`context` 겹침 → 따뜻한 맥락), 다음 것을 막고 있다(`blocks`).
        # 영수증을 찾다가 "아 보험도 청구해야지"가 나오는 것은 실제로 일어나는 일이다.
        {
            "name": "실손보험 청구", "deadline": today, "size": "S",
            "blocking": 0, "reentry": None, "muted": None, "needs_prep": False,
            "context": ["서류함", "보험앱"],
            "blocks": ["치과 진료비 가계부 정리"],
            "spawned_by": "치과 진료비 영수증 찾기", "source_utterance": None,
        },
        {
            "name": "치과 진료비 가계부 정리", "deadline": None, "size": "S",
            "blocking": 0, "reentry": None, "muted": None, "needs_prep": False,
            "context": ["가계부"],
            "blocks": [], "spawned_by": None, "source_utterance": None,
        },
        {
            "name": "어머니 생신 선물 고르기", "deadline": None, "size": "M",
            "blocking": 0, "reentry": None, "muted": None, "needs_prep": False,
            "context": ["어머니"],
            "blocks": ["생신 카드 쓰기"], "spawned_by": None, "source_utterance": None,
        },
        {
            "name": "생신 카드 쓰기", "deadline": None, "size": "S",
            "blocking": 0, "reentry": None, "muted": None, "needs_prep": False,
            "context": ["어머니"],
            "blocks": [], "spawned_by": None, "source_utterance": None,
        },
        {
            "name": "건강검진 예약 전화", "deadline": None, "size": "S",
            "blocking": 0, "reentry": None, "muted": None, "needs_prep": False,
            "context": ["병원"],
            "blocks": [], "spawned_by": None, "source_utterance": None,
        },
        {
            "name": "분리수거", "deadline": None, "size": "S",
            "blocking": 0, "reentry": None, "muted": None, "needs_prep": False,
            "context": ["베란다"],
            "blocks": [], "spawned_by": None, "source_utterance": None,
        },
        {
            "name": "전세 계약 만기 확인", "deadline": None, "size": "M",
            "blocking": 0, "reentry": None, "muted": None, "needs_prep": False,
            "context": ["서류함"],
            "blocks": [], "spawned_by": None, "source_utterance": None,
        },
    ]


def cmd_seed(args):
    """시연용 상태를 만든다. **제품 흐름이 아니라 데모 도구다.**

    빈 상태에서는 카드가 언제나 콜드 스타트로 나와서 우선순위 엔진도 그래프도
    화면에 드러나지 않는다. 심사자가 한 커맨드로 완성된 흐름을 볼 수 있게 한다.

    기존 상태가 있으면 덮지 않는다 — 실제 사용자의 항목을 시연 데이터로 지우는 것이
    이 커맨드가 낼 수 있는 유일한 큰 사고다. `--force`를 요구한다.
    """
    state = state_manager.load(args.state)

    if state.get("open_items") and not args.force:
        _output({
            "build": BUILD,
            "error": "state_not_empty",
            "message": f"이미 항목이 {len(state['open_items'])}개 있다. 덮어쓰려면 --force",
        })
        sys.exit(1)

    now = datetime.now()
    today = now.strftime("%m-%d")

    state["open_items"] = _demo_items(today)
    state["open_card"] = None
    state["active_token"] = None
    # 직전에 완료한 항목. 이것의 맥락이 `따뜻한 맥락` 가점을 켠다.
    # "실손보험 청구"가 여기서 파생됐다(`spawned_by`).
    # `completed_at` 형식은 harvest_processor 와 같아야 한다. ISO로 쓰면
    # "오늘 완료" 집계가 이 항목을 놓친다.
    state["completed"] = [{
        "name": "치과 진료비 영수증 찾기",
        "completed_at": now.strftime("%m-%d %H:%M"),
    }]
    state["last_context"] = ["서류함", "보험앱"]
    state["rejection_log"] = []

    # 캘린더 스냅샷도 함께 심는다. 커넥터 없이도 창 계산과 크기 상한이 보인다.
    # 하루에 일정이 하나뿐인 사람은 드물어서 오후 것도 함께 둔다 — 다음 일정만
    # 보이면 창이 왜 좁은지는 알아도 하루가 어떤 모양인지는 안 보인다.
    if args.busy_in is not None:
        start = now + timedelta(minutes=args.busy_in)
        end = start + timedelta(minutes=60)
        later_start = end + timedelta(minutes=150)
        later_end = later_start + timedelta(minutes=30)
        state["calendar_snapshot"] = {
            "fetched_at": now.isoformat(),
            "clock_delta": 0.0,
            "utc_offset": (now.astimezone().utcoffset() or timedelta()).total_seconds(),
            "busy": [
                f"{start:%H:%M}~{end:%H:%M}",
                f"{later_start:%H:%M}~{later_end:%H:%M}",
            ],
        }
        # 캘린더를 이미 받은 상태이므로 맨몸 `start`가 게이트에 걸리지 않는다.
        state["calendar_prompted_at"] = now.isoformat()

    _, mode, calibration, window = _session_context(state)

    result = {
        "build": BUILD,
        "ok": True,
        "seeded": [item["name"] for item in state["open_items"]],
        "open_items_count": len(state["open_items"]),
        "today_deadline_count": sum(1 for i in state["open_items"] if i.get("deadline") == today),
        "last_context": state["last_context"],
        "mode": mode,
        "calibration": calibration,
        "window": window,
        "note": "시연용 데이터다. 지우려면 상태 파일을 삭제한다.",
    }
    _save(args.state, state, result)
    _output(result)


def cmd_status(args):
    state = state_manager.load(args.state)
    _, mode, _, window = _session_context(state)

    items_summary = [
        {"name": item["name"], "size": item.get("size"), "deadline": item.get("deadline")}
        for item in state.get("open_items", [])
    ]

    _output({
        "open_card": state.get("open_card"),
        "open_items": items_summary,
        "open_items_count": len(state.get("open_items", [])),
        "completed_count": len(state.get("completed", [])),
        "rejection_log_count": len(state.get("rejection_log", [])),
        "mode": mode,
        "window": window,
    })


def main():
    parser = argparse.ArgumentParser(
        prog="simple_tasks",
        description="Simple Tasks CLI - 상태 관리와 의사결정",
    )
    parser.add_argument(
        "--state", default=_default_state_path(),
        help="상태 파일 경로 (기본: scripts/.state/tasks.json)",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # start
    start_parser = subparsers.add_parser("start", help="세션 시작 (load + 첫 카드 선택)")
    start_parser.add_argument("--now",
                               help="커넥터가 준 현재 시각(ISO). 서버 타임존이 달라도 모드가 맞는다")
    start_parser.add_argument("--busy", action="append",
                               help='캘린더 busy 구간. "11:00~11:40" 또는 ISO 쌍. 여러 번 쓸 수 있다')
    start_parser.add_argument("--no-calendar", action="store_true",
                               help="캐시된 캘린더 스냅샷을 버리고 시각만으로 판단한다")

    # load
    subparsers.add_parser("load", help="상태 파일 로드 및 세션 시작")

    # pick
    pick_parser = subparsers.add_parser("pick", help="다음 카드 선택")
    pick_parser.add_argument("--first", action="store_true", help="첫 카드 여부")

    # complete
    complete_parser = subparsers.add_parser("complete", help="항목 완료 처리")
    complete_parser.add_argument("item", help="완료 항목 이름")
    complete_parser.add_argument("--token", required=True, help="pick에서 받은 완료 토큰")
    complete_parser.add_argument("--reentry", help="재진입 메모")
    complete_parser.add_argument("--new-idea", help="하다가 떠오른 것")
    complete_parser.add_argument("--next", action="store_true",
                                  help="이어서 다음 카드를 고르고 새 토큰을 함께 반환")

    # reject
    reject_parser = subparsers.add_parser("reject", help="항목 거부 기록")
    reject_parser.add_argument("item", help="거부 항목 이름")
    reject_parser.add_argument("--token", required=True, help="pick에서 받은 토큰")
    reject_parser.add_argument("--reason", required=True,
                                help="사유: too_big, abandon, done_partial, 또는 한국어 사유 문자열")
    reject_parser.add_argument("--next", action="store_true",
                                help="이어서 다음 카드를 고르고 새 토큰을 함께 반환")

    # add
    add_parser = subparsers.add_parser("add", help="항목 추가")
    add_parser.add_argument("item", help="항목 이름")
    add_parser.add_argument("--size", help="크기: S, M, L (기본: M)")
    add_parser.add_argument("--deadline", help="마감: 08-30 18:00")
    add_parser.add_argument("--blocking", type=int, help="차단중 수 (이름을 모를 때만)")
    add_parser.add_argument("--reentry", help="재진입 메모")
    add_parser.add_argument("--context", action="append",
                             help="맥락 노드(파일·사람·도구). 여러 번 쓸 수 있다")
    add_parser.add_argument("--blocks", action="append",
                             help="이 항목이 막고 있는 항목명. 여러 번 쓸 수 있다")
    add_parser.add_argument("--spawned-by", help="이 항목이 나온 부모 항목명")
    add_parser.add_argument("--utterance",
                             help="사용자 발화 원문. 검문과 매칭에 쓴다. 되도록 항상 넘긴다")
    add_parser.add_argument("--no-match", action="store_true",
                             help="기존 항목 매칭을 건너뛰고 새로 만든다")
    add_parser.add_argument("--strict-name", action="store_true",
                             help="발화에 없는 어절을 실제로 잘라낸다 (기본은 보고만)")
    add_parser.add_argument("--next", action="store_true",
                             help="추가한 뒤 바로 카드를 고르고 토큰을 함께 반환")
    add_parser.add_argument("--first", action="store_true",
                             help="--next 와 함께 쓸 때 첫 카드 기준으로 고른다")

    # schedule — 결선 이후. 사용자가 말한 일정을 캘린더에 등록할 준비를 한다.
    # schedule_parser = subparsers.add_parser(
    #     "schedule", help="사용자가 말한 일정을 캘린더에 등록할 준비 (검문 + 인자 조립)")
    # schedule_parser.add_argument("--utterance",
    #                               help="사용자 발화 원문. 없으면 만들지 않는다")
    # schedule_parser.add_argument("--title", help="일정 제목. 발화에 근거가 있어야 한다")
    # schedule_parser.add_argument("--start",
    #                               help='시작 시각. 오프셋 붙인 ISO. 예: "2026-08-31T15:00:00+09:00"')
    # schedule_parser.add_argument("--end",
    #                               help=f"종료 시각. 없으면 {event_builder.DEFAULT_DURATION_MINUTES}분으로 두고 확인 문구에 표시한다")
    # schedule_parser.add_argument("--now", help="현재 시각(오프셋 포함). 지난 시각 경고에 쓴다")
    # schedule_parser.add_argument("--commit", action="store_true",
    #                               help="등록이 끝난 뒤 기록한다. --token 이 필요하다")
    # schedule_parser.add_argument("--token", help="schedule 이 발급한 일회용 토큰")
    # schedule_parser.add_argument("--event-id", help="커넥터가 돌려준 이벤트 id")

    # open-card
    open_card_parser = subparsers.add_parser("open-card", help="카드 열기")
    open_card_parser.add_argument("item", help="항목 이름")

    # close-card
    subparsers.add_parser("close-card", help="카드 닫기")

    # end-session
    subparsers.add_parser("end-session", help="세션 종료")

    # status
    subparsers.add_parser("status", help="현재 상태 요약")

    # seed — 시연용. 제품 흐름이 아니다.
    seed_parser = subparsers.add_parser("seed", help="시연용 항목·그래프·캘린더를 심는다")
    seed_parser.add_argument("--force", action="store_true",
                              help="기존 항목이 있어도 덮어쓴다")
    seed_parser.add_argument("--busy-in", type=int, default=40,
                              help="지금부터 N분 뒤에 60분짜리 일정을 둔다 (기본 40)")

    args = parser.parse_args()

    commands = {
        "start": cmd_start,
        "load": cmd_load,
        "pick": cmd_pick,
        "complete": cmd_complete,
        "add": cmd_add,
        # "schedule": cmd_schedule,   # 결선 이후
        "reject": cmd_reject,
        "open-card": cmd_open_card,
        "close-card": cmd_close_card,
        "end-session": cmd_end_session,
        "status": cmd_status,
        "seed": cmd_seed,
    }

    cmd_func = commands.get(args.command)
    if not cmd_func:
        parser.print_help()
        sys.exit(1)

    # **stdout을 비운 채로 죽지 않는다.** 빈 출력은 런타임에서 "스크립트가 없다"로
    # 읽히고, 모델은 원인을 찾으러 디렉터리를 뒤지기 시작한다. 실제로 그 사고가 났다.
    # 무슨 일이 있어도 JSON 한 덩이는 나가야 한다.
    try:
        cmd_func(args)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        _output({
            "error": type(exc).__name__,
            "message": str(exc),
            "hint": "상태를 쓸 수 없으면 SIMPLE_TASKS_STATE로 쓰기 가능한 경로를 지정한다",
        })
        sys.exit(1)


if __name__ == "__main__":
    main()
