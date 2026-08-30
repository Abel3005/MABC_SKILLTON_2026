"""Simple Tasks CLI 진입점.

모든 출력은 JSON. argparse 서브커맨드로 기능을 노출한다.
"""

import argparse
import json
import sys
from datetime import datetime
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


def _default_state_path():
    return str(SCRIPT_DIR / ".state" / "tasks.json")


def _output(data):
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _pick_with_token(state, is_first, just_harvested=None):
    """다음 항목을 고르고 토큰을 발급한다. state를 제자리에서 수정한다.

    호출자가 이후에 state_manager.save를 반드시 호출해야 한다.
    """
    now = datetime.now()
    mode = mode_detector.detect(now.hour, now.minute)
    calibration = size_calibrator.calibrate(
        state.get("rejection_log", []),
        mode["default_size"],
    )

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
        }

    token = token_manager.generate()
    state["active_token"] = {
        "hash": token_manager.hash_token(token),
        "item": result["name"],
        "created_at": now.isoformat(),
    }

    result["mode"] = mode
    result["calibration"] = calibration
    result["token"] = token
    return result


def cmd_load(args):
    state = state_manager.load(args.state)
    session_info = session_controller.start_session(state)
    # 묵힘 해제 후 저장
    state_manager.save(args.state, state)
    _output(session_info)


def cmd_start(args):
    """load + pick --first 를 한 번에. 세션 시작용."""
    state = state_manager.load(args.state)
    session_info = session_controller.start_session(state)

    # 열린 카드가 있으면 이탈 회수가 먼저이므로 카드를 고르지 않는다
    if session_info["has_open_card"]:
        session_info["card"] = None
    else:
        session_info["card"] = _pick_with_token(state, is_first=True)

    state_manager.save(args.state, state)
    _output(session_info)


def cmd_pick(args):
    state = state_manager.load(args.state)
    result = _pick_with_token(state, is_first=args.first)
    state_manager.save(args.state, state)
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

    state_manager.save(args.state, state)
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

    state_manager.save(args.state, state)
    _output(result)


def cmd_open_card(args):
    state = state_manager.load(args.state)
    result = session_controller.open_card(state, args.item)
    state_manager.save(args.state, result["state"])
    del result["state"]
    _output(result)


def cmd_close_card(args):
    state = state_manager.load(args.state)
    result = session_controller.close_card(state)
    state_manager.save(args.state, result["state"])
    del result["state"]
    _output(result)


def cmd_end_session(args):
    state = state_manager.load(args.state)
    result = session_controller.end_session(state)
    state_manager.save(args.state, result["state"])
    del result["state"]
    _output(result)


def cmd_add(args):
    state = state_manager.load(args.state)
    item = {
        "name": args.item,
        "deadline": args.deadline,
        "size": args.size or "M",
        "blocking": args.blocking or 0,
        "reentry": args.reentry,
        "muted": None,
        "needs_prep": False,
        # 엣지 필드. 사용자가 발화한 것만 들어간다. 추론해서 채우지 않는다.
        "context": args.context or [],
        "blocks": args.blocks or [],
        "spawned_by": args.spawned_by,
    }
    state.setdefault("open_items", []).append(item)

    result = {
        "action": "added",
        "item": item,
        "open_items_count": len(state["open_items"]),
    }

    if args.next:
        result["card"] = _pick_with_token(state, is_first=args.first)

    state_manager.save(args.state, state)
    _output(result)


def cmd_status(args):
    state = state_manager.load(args.state)
    from datetime import datetime
    now = datetime.now()
    mode = mode_detector.detect(now.hour, now.minute)

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
    subparsers.add_parser("start", help="세션 시작 (load + 첫 카드 선택)")

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
    if cmd_func:
        cmd_func(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
