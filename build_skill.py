#!/usr/bin/env python3
"""simple-tasks 스킬을 업로드용 zip으로 묶고 내용물을 검증한다.

    python build_skill.py                # zip 생성 + 검증
    python build_skill.py --check-only   # 기존 zip만 검증 (새로 만들지 않음)
    python build_skill.py --prefix       # zip 안에 simple-tasks/ 폴더를 두고 담는다

손으로 압축하면 scripts/ 가 통째로 빠져도 알 수 없다. 실제로 그 사고가 한 번 났고,
런타임에서는 "스킬이 설치되지 않았다"로만 보였다. 그것을 막는 것이 이 스크립트의 목적이다.

검증에 실패하면 종료 코드 1로 끝나고 깨진 zip을 남기지 않는다.
"""

import argparse
import py_compile
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SKILL_DIR = ROOT / "simple-tasks"
ZIP_PATH = ROOT / "simple-tasks.zip"

# 이게 없으면 스킬이 동작하지 않는다. zip 안에서 다시 확인한다.
REQUIRED = [
    "SKILL.md",
    "references/commands.md",
    "references/design-notes.md",
    "references/modes.md",
    "references/responses.md",
    "scripts/__init__.py",
    "scripts/calendar_window.py",
    "scripts/codec.py",
    "scripts/harvest_processor.py",
    "scripts/item_matcher.py",
    "scripts/mode_detector.py",
    "scripts/priority_engine.py",
    "scripts/session_controller.py",
    "scripts/simple_tasks.py",
    "scripts/size_calibrator.py",
    "scripts/state_manager.py",
    "scripts/token_manager.py",
    "scripts/utterance_guard.py",
]

# 있으면 함께 담지만 없어도 실패로 보지 않는다.
OPTIONAL = ["README.md"]

# 사용자 상태와 빌드 부산물. 하나라도 새어 들어가면 실패로 본다.
FORBIDDEN_PARTS = {"__pycache__", ".state", ".claude", ".git"}
FORBIDDEN_SUFFIXES = {".pyc", ".pyo", ".zip", ".bak"}
FORBIDDEN_NAMES = {"state.md", "todo.md"}


def _is_forbidden(rel: Path) -> bool:
    if FORBIDDEN_PARTS & set(rel.parts):
        return True
    if rel.suffix in FORBIDDEN_SUFFIXES:
        return True
    return rel.name in FORBIDDEN_NAMES


def collect_sources():
    """담을 파일 목록을 만든다. (상대경로 문자열, 절대경로) 쌍."""
    if not SKILL_DIR.is_dir():
        fail([f"스킬 폴더가 없다: {SKILL_DIR}"])

    files = []
    for path in sorted(SKILL_DIR.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(SKILL_DIR)
        if _is_forbidden(rel):
            continue
        files.append((rel.as_posix(), path))
    return files


def check_sources(files):
    """zip을 만들기 전에 원본을 검사한다."""
    problems = []
    present = {rel for rel, _ in files}

    missing = [r for r in REQUIRED if r not in present]
    if missing:
        problems.append("필수 파일 누락: " + ", ".join(missing))

    # 매니페스트에 없는 파일은 담지 않는다. 블랙리스트는 늘 한발 늦는다 — 실제로
    # 테스트 잔재(_s/c.json, 활성 토큰까지 든 상태 파일)가 업로드 zip에 실려
    # 나간 적이 있다. 무엇이 올라가는지 매번 정확히 알고 있어야 한다.
    unexpected = sorted(present - set(REQUIRED) - set(OPTIONAL))
    if unexpected:
        problems.append(
            "매니페스트에 없는 파일: " + ", ".join(unexpected)
            + "  → 스킬에 필요하면 REQUIRED에 추가하고, 아니면 지운다"
        )

    # 파이썬 파일이 실제로 컴파일되는지. 깨진 소스를 올리는 것이 없는 것보다 나쁘다.
    for rel, path in files:
        if path.suffix != ".py":
            continue
        try:
            py_compile.compile(str(path), cfile=str(Path(tempfile.gettempdir()) / "sk.pyc"),
                               doraise=True)
        except py_compile.PyCompileError as exc:
            problems.append(f"컴파일 실패 {rel}: {exc.msg.strip().splitlines()[-1]}")

    # SKILL.md 프론트매터. name/description 이 없으면 런타임이 스킬을 못 찾는다.
    skill_md = SKILL_DIR / "SKILL.md"
    if skill_md.exists():
        text = skill_md.read_text(encoding="utf-8")
        match = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
        if not match:
            problems.append("SKILL.md에 YAML 프론트매터(--- ... ---)가 없다")
        else:
            front = match.group(1)
            for key in ("name", "description"):
                if not re.search(rf"^{key}\s*:", front, re.MULTILINE):
                    problems.append(f"SKILL.md 프론트매터에 {key}가 없다")

    return problems


def build(files, prefix: bool):
    """zip을 만든다. 검증 전이므로 임시 파일에 쓴다."""
    tmp = ZIP_PATH.with_suffix(".zip.tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel, path in files:
            arcname = f"{SKILL_DIR.name}/{rel}" if prefix else rel
            zf.write(path, arcname)
    return tmp


def check_zip(zip_path: Path, prefix: bool):
    """만들어진 산출물을 다시 열어 검사한다. 원본이 아니라 이것이 올라간다."""
    problems = []
    with zipfile.ZipFile(zip_path) as zf:
        bad = zf.testzip()
        if bad is not None:
            problems.append(f"zip이 손상되었다: {bad}")
        names = set(zf.namelist())

    def arc(rel):
        return f"{SKILL_DIR.name}/{rel}" if prefix else rel

    missing = [r for r in REQUIRED if arc(r) not in names]
    if missing:
        problems.append("zip 안에 필수 파일 없음: " + ", ".join(missing))

    leaked = [n for n in sorted(names) if _is_forbidden(Path(n))]
    if leaked:
        problems.append("담기면 안 되는 파일: " + ", ".join(leaked[:5]))

    return problems, names


def fail(problems):
    print("\n[실패]")
    for p in problems:
        print(f"  - {p}")
    sys.exit(1)


def main():
    ap = argparse.ArgumentParser(description="simple-tasks 스킬 패키징 및 검증")
    ap.add_argument("--check-only", action="store_true",
                    help="새로 만들지 않고 기존 zip만 검증한다")
    ap.add_argument("--prefix", action="store_true",
                    help="zip 안에 simple-tasks/ 폴더를 두고 담는다")
    args = ap.parse_args()

    if args.check_only:
        if not ZIP_PATH.exists():
            fail([f"검증할 zip이 없다: {ZIP_PATH}"])
        problems, names = check_zip(ZIP_PATH, args.prefix)
        if problems:
            fail(problems)
        print(f"[OK] {ZIP_PATH.name} — 파일 {len(names)}건, 필수 {len(REQUIRED)}건 모두 확인")
        return

    files = collect_sources()

    problems = check_sources(files)
    if problems:
        fail(problems)

    tmp = build(files, args.prefix)

    problems, names = check_zip(tmp, args.prefix)
    if problems:
        tmp.unlink(missing_ok=True)
        fail(problems)

    shutil.move(str(tmp), str(ZIP_PATH))

    optional_in = [o for o in OPTIONAL if any(n.endswith(o) for n in names)]
    layout = f"{SKILL_DIR.name}/… (폴더 포함)" if args.prefix else "SKILL.md, scripts/… (최상위)"

    print(f"[OK] {ZIP_PATH.name}  ({ZIP_PATH.stat().st_size:,} bytes)")
    print(f"  필수 {len(REQUIRED)}건 전부 포함, 총 {len(names)}건")
    if optional_in:
        print(f"  선택 포함: {', '.join(optional_in)}")
    print(f"  zip 내부 구조: {layout}")
    print("  플랫폼이 폴더째 받는 형식이면 --prefix 를 붙여 다시 만든다.")


if __name__ == "__main__":
    main()
