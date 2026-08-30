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
    print(json.dumps(data, ensure_ascii=False, indent=2))


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
    now, mode, calibration, window = _session_context(state)

    session_info = session_controller.start_session(state, now=now)
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

    # open-card
    open_card_parser = subparsers.add_parser("open-card", help="카드 열기")
    open_card_parser.add_argument("item", help="항목 이름")

    # close-card
    subparsers.add_parser("close-card", help="카드 닫기")

    # end-session
    subparsers.add_parser("end-session", help="세션 종료")

    # status
    subparsers.add_parser("status", help="현재 상태 요약")

    args = parser.parse_args()

    commands = {
        "start": cmd_start,
        "load": cmd_load,
        "pick": cmd_pick,
        "complete": cmd_complete,
        "add": cmd_add,
        "reject": cmd_reject,
        "open-card": cmd_open_card,
        "close-card": cmd_close_card,
        "end-session": cmd_end_session,
        "status": cmd_status,
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
