"""상태 파일(JSON + base64) 매니저.

내부 저장은 JSON + base64 인코딩.
공개 API(load, save)는 평문 dict를 주고받으므로
다른 모듈은 변경 없이 동작한다.
"""

import json
import re
from pathlib import Path

from scripts import codec

_FORMAT_VERSION = "simple-tasks-v2"


def _empty_state():
    return {
        "open_card": None,
        "open_items": [],
        "completed": [],
        "rejection_log": [],
        "active_token": None,
        # 직전에 완료한 항목의 맥락. 다음 카드의 "따뜻한 맥락" 가점에 쓴다.
        "last_context": [],
        # 세션당 1회 받아 두는 캘린더 busy 구간. 시각 정보뿐이라 인코딩하지 않는다 —
        # base64는 항목 이름을 모델에게서 가리려는 장치인데 여기엔 이름이 없다.
        # 일정 제목은 애초에 받지 않는다.
        "calendar_snapshot": None,
    }


def load(path):
    """파일 읽어서 디코딩. 없으면 빈 구조 반환.

    기존 state.md가 있으면 자동 마이그레이션한다.
    """
    p = Path(path)

    # 새 JSON 파일이 없으면 예전 위치에서 옮겨 온다.
    if not p.exists():
        scripts_dir = Path(__file__).resolve().parent

        # 예전에는 스킬 패키지 안(scripts/.state/)에 저장했다. 읽기 전용 설치에서
        # 쓰기가 실패해 카드가 통째로 죽었기 때문에 홈 디렉터리로 옮겼다.
        legacy_json = scripts_dir / ".state" / "tasks.json"
        if legacy_json.exists() and legacy_json != p:
            try:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(legacy_json.read_text(encoding="utf-8"), encoding="utf-8")
            except OSError:
                # 옮기지 못해도 읽기는 된다. 그대로 읽어서 쓴다.
                p = legacy_json

        old_md = scripts_dir.parent / "state.md"
        if not p.exists() and old_md.exists():
            _migrate_from_markdown(str(old_md), path)
        elif not p.exists():
            return _empty_state()

    # JSON 파일이 있으면 (마이그레이션 직후 포함) 읽기
    if not p.exists():
        return _empty_state()

    raw = p.read_text(encoding="utf-8")
    data = json.loads(raw)

    state = _empty_state()
    state["active_token"] = data.get("active_token")
    state["last_context"] = codec.decode_list(data.get("last_context")) or []
    state["calendar_snapshot"] = data.get("calendar_snapshot")

    # open_card 디코딩
    if data.get("open_card"):
        card = dict(data["open_card"])
        card["name"] = codec.decode_text(card.get("name"))
        if "opened_at" in card:
            card["opened_at"] = codec.decode_text(card.get("opened_at"))
        state["open_card"] = card

    # open_items 디코딩
    for item in data.get("open_items", []):
        state["open_items"].append(codec.decode_item(item))

    # completed 디코딩
    for comp in data.get("completed", []):
        decoded = dict(comp)
        decoded["name"] = codec.decode_text(decoded.get("name"))
        if "completed_at" in decoded:
            decoded["completed_at"] = codec.decode_text(decoded.get("completed_at"))
        state["completed"].append(decoded)

    # rejection_log 디코딩
    for log in data.get("rejection_log", []):
        decoded = dict(log)
        decoded["date"] = codec.decode_text(decoded.get("date"))
        decoded["name"] = codec.decode_text(decoded.get("name"))
        decoded["reason"] = codec.decode_text(decoded.get("reason"))
        state["rejection_log"].append(decoded)

    return state


def save(path, state):
    """dict를 인코딩하여 JSON으로 저장."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "_format": _FORMAT_VERSION,
        "_encoding": "base64",
        "active_token": state.get("active_token"),
        "last_context": codec.encode_list(state.get("last_context") or []),
        "calendar_snapshot": state.get("calendar_snapshot"),
    }

    # open_card 인코딩
    if state.get("open_card"):
        card = dict(state["open_card"])
        card["name"] = codec.encode_text(card.get("name"))
        if "opened_at" in card:
            card["opened_at"] = codec.encode_text(card.get("opened_at"))
        data["open_card"] = card
    else:
        data["open_card"] = None

    # open_items 인코딩
    data["open_items"] = [
        codec.encode_item(item) for item in state.get("open_items", [])
    ]

    # completed 인코딩
    encoded_completed = []
    for comp in state.get("completed", []):
        encoded = dict(comp)
        encoded["name"] = codec.encode_text(encoded.get("name"))
        if "completed_at" in encoded:
            encoded["completed_at"] = codec.encode_text(encoded.get("completed_at"))
        encoded_completed.append(encoded)
    data["completed"] = encoded_completed

    # rejection_log 인코딩
    encoded_log = []
    for log in state.get("rejection_log", []):
        encoded = dict(log)
        encoded["date"] = codec.encode_text(encoded.get("date"))
        encoded["name"] = codec.encode_text(encoded.get("name"))
        encoded["reason"] = codec.encode_text(encoded.get("reason"))
        encoded_log.append(encoded)
    data["rejection_log"] = encoded_log

    p.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ---------- 마이그레이션 ----------

def _parse_md_item(line):
    """마크다운 항목 줄 파싱 (마이그레이션 전용)."""
    item = {
        "name": "",
        "deadline": None,
        "size": "M",
        "blocking": 0,
        "reentry": None,
        "muted": None,
        "needs_prep": False,
    }

    text = re.sub(r"^-\s*\[[ x]\]\s*", "", line).strip()
    parts = [p.strip() for p in text.split("|")]
    item["name"] = parts[0]

    for part in parts[1:]:
        if part.startswith("마감"):
            item["deadline"] = part.replace("마감", "").strip()
        elif part.startswith("크기"):
            item["size"] = part.replace("크기", "").strip()
        elif part.startswith("차단중"):
            try:
                item["blocking"] = int(part.replace("차단중", "").strip())
            except ValueError:
                item["blocking"] = 0
        elif part.startswith("묵힘"):
            muted_match = re.search(r"\((.+)\)", part)
            item["muted"] = muted_match.group(1) if muted_match else "true"
        elif part.startswith("준비 필요"):
            item["needs_prep"] = True

    return item


def _migrate_from_markdown(old_path, new_path):
    """기존 state.md를 JSON으로 마이그레이션 후 .bak으로 이름 변경."""
    p = Path(old_path)
    text = p.read_text(encoding="utf-8")
    state = _empty_state()

    section = None
    pending_item = None

    for raw_line in text.split("\n"):
        line = raw_line.strip()

        if line.startswith("## 열린 카드"):
            section = "open_card"
            continue
        elif line.startswith("## 열린 항목"):
            section = "open_items"
            continue
        elif line.startswith("## 완료"):
            section = "completed"
            continue
        elif line.startswith("## 거부 로그"):
            section = "rejection_log"
            continue
        elif line.startswith("## ") or line.startswith("# "):
            section = None
            continue

        if not line:
            continue

        if section == "open_card":
            if line != "(없음)":
                card_parts = [cp.strip() for cp in line.split("|")]
                state["open_card"] = {
                    "name": card_parts[0],
                    "opened_at": card_parts[1] if len(card_parts) > 1 else "",
                }

        elif section == "open_items":
            if line.startswith("- [ ]"):
                if pending_item is not None:
                    state["open_items"].append(pending_item)
                pending_item = _parse_md_item(line)
            elif line.startswith("재진입:") and pending_item is not None:
                pending_item["reentry"] = line.replace("재진입:", "").strip()

        elif section == "completed":
            if line.startswith("- [x]"):
                text_content = re.sub(r"^-\s*\[x\]\s*", "", line).strip()
                comp_parts = [cp.strip() for cp in text_content.split("|")]
                state["completed"].append({
                    "name": comp_parts[0],
                    "completed_at": comp_parts[1] if len(comp_parts) > 1 else "",
                })

        elif section == "rejection_log":
            log_parts = [lp.strip() for lp in line.split("|")]
            if len(log_parts) >= 3:
                state["rejection_log"].append({
                    "date": log_parts[0],
                    "name": log_parts[1],
                    "reason": log_parts[2],
                })

    if pending_item is not None:
        state["open_items"].append(pending_item)

    # 새 형식으로 저장
    save(new_path, state)

    # 원본을 .bak으로 이름 변경
    bak = Path(old_path + ".bak")
    p.rename(bak)
