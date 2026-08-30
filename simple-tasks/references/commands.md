# 커맨드 레퍼런스

모든 출력은 JSON. 작업 디렉터리는 스킬 루트(`SKILL.md`가 있는 곳)를 기준으로 한다.

상태 저장 경로는 기본이 `scripts/.state/tasks.json`이고 `--state <경로>`로 바꿀 수 있다.

## 상태 파일 정책

**상태 파일을 직접 읽거나 수정하지 않는다.** 항목 텍스트는 base64로 인코딩되어 저장되므로 파일을 열어도 읽을 수 없고, 열어서 읽는 것 자체가 "목록을 출력하지 않는다"는 규칙을 우회하는 행위다. 항목 내용은 `pick`과 `status`의 출력으로만 확인한다.

## 토큰 규약

`pick`이 일회용 토큰을 발급하고 `complete`와 `reject`가 그것을 검증한다. 상태 파일에는 해시만 남는다.

- 토큰은 **직전 `pick`이 고른 항목 하나에만** 유효하다. 항목 이름이 다르면 `item_mismatch`로 거부된다.
- 한 번 쓰면 소멸한다. 재사용하면 `no_active_token`이 나온다.
- 다음 카드를 내려면 `pick`을 다시 호출해 새 토큰을 받는다.

이 장치 때문에 **`pick`을 거치지 않은 완료는 기록될 수 없다.** 완료 여부를 모델이 임의로 판단하는 경로가 구조적으로 막혀 있다.

에러 응답:

| `error` | 뜻 | 대응 |
|---|---|---|
| `no_active_token` | 활성 토큰 없음 (미발급 또는 이미 사용) | `pick`을 먼저 호출한다 |
| `invalid_token` | 토큰 불일치 | `pick`을 다시 호출해 새 토큰을 받는다 |
| `item_mismatch` | 토큰과 항목 이름이 다름 | 토큰이 가리키는 항목명을 그대로 쓴다 |

## 왕복 줄이기

커맨드 한 번이 1초 안팎이다. 카드가 뜨는 속도가 곧 체감 품질이므로 **항상 합친 형태를 쓴다.**

| 하려는 일 | 쓸 것 | 쓰지 말 것 |
|---|---|---|
| 세션 시작 | `start` | `load` + `pick --first` |
| 반응 기록 후 다음 카드 | `complete/reject ... --next` | `complete/reject` + `pick` |
| 항목 등록 후 카드 | `add ... --next` | `add` + `pick` |

`load`와 `pick`은 개별 진단용으로 남겨 둔 것이지 정상 흐름에서 쓰는 커맨드가 아니다.

## start

```bash
python scripts/simple_tasks.py start
```

세션을 시작한다. `묵힘` 표시를 해제하고, 열린 카드가 없으면 첫 카드까지 골라 토큰을 발급한다.

반환: `has_open_card`, `open_card`, `open_count`, `today_deadline_count`, `mode`, `calibration`, `muted_released`, `card`.

`has_open_card`가 true면 `card`는 null이다. 이탈 회수가 먼저이기 때문이다.

## load

```bash
python scripts/simple_tasks.py load
```

`start`에서 카드 선택만 뺀 것. 진단용.

## pick

```bash
python scripts/simple_tasks.py pick [--first]
```

다음에 낼 항목을 고르고 토큰을 발급한다. `--first`는 세션의 첫 카드일 때만 붙인다(선택 기준이 달라진다).

반환: `name`, `token`, `why`, `mode`, `calibration`, `score_detail`.
후보가 없으면 `{"name": null, "reason": "no_candidates", ...}`.

`why`는 `이유` 줄에 쓸 사실이다. [항목 그래프](#항목-그래프) 참조.

## add

```bash
python scripts/simple_tasks.py add "항목명" [--size S|M|L] [--deadline "08-30 18:00"] [--reentry "메모"] [--context "보고서.docx"] [--blocks "김팀장 회신"] [--spawned-by "부모 항목"] [--blocking 2] [--next] [--first]
```

항목을 등록한다. 기본 크기는 `M`. 마감은 `MM-DD` 또는 `MM-DD HH:MM`.

`--context`와 `--blocks`는 여러 번 쓸 수 있다. `--blocking`은 막고 있는 것의 **이름을 모를 때만** 쓰는 옛 방식이고, 이름을 알면 `--blocks`를 쓴다.

`--next`를 붙이면 등록 직후 카드를 고르고 토큰을 발급해 `card` 필드로 돌려준다. `--first`는 `--next`와 함께 쓸 때 첫 카드 기준으로 고르라는 뜻이다. 콜드 스타트에서 `add "..." --next --first` 한 번으로 등록과 카드 준비가 끝난다.

사용자가 "이거 추가해줘", "할 일 넣어줘"라고 하거나 **"~해야해"류의 선언을 하면** 자연어에서 이름·크기·마감을 뽑아 이 커맨드로 넘긴다. 등록만 하고 끝내지 말고 이어서 세션을 시작한다.

## complete

```bash
python scripts/simple_tasks.py complete "항목명" --token TOKEN [--reentry "메모"] [--new-idea "아이디어"] [--next]
```

- `--reentry` **없으면** 항목이 완료로 이동한다.
- `--reentry` **있으면** 항목이 남고 재진입 메모만 갱신된다(아직 안 끝났다는 뜻).
- `--new-idea`는 새 항목으로 추가되며 `묵힘` 표시가 자동으로 붙는다.
- `--next`는 다음 카드와 새 토큰을 `card` 필드로 함께 돌려준다. 방금 `--new-idea`로 넣은 항목은 후보에서 제외된다.

## reject

```bash
python scripts/simple_tasks.py reject "항목명" --token TOKEN --reason REASON [--next]
```

`--reason`에 넣을 값:

| 값 | 기록되는 사유 | 언제 |
|---|---|---|
| `too_big` | 너무 큼 | `너무 큼` 버튼 |
| `abandon` | 이탈 | 이탈 회수의 `손도 못 댐` |
| `done_partial` | 하다 말았음 | 참고용. 보통 `complete --reentry`를 쓴다 |
| `자료 없음` | 자료 없음 | `다른 거`의 사유 |
| `마음이 안 감` / `더 급한 게 있음` / `그냥 아님` | 입력한 문자열 그대로 | `다른 거`의 사유 |

**`too_big`은 언더스코어다.** 하이픈으로 쓰면 문자열이 그대로 기록되어 크기 보정 집계에 잡히지 않는다.

`다른 거`의 사유는 한국어 문자열을 **그대로** 넘긴다. `other`로 뭉뚱그리면 `그냥 아님`으로 기록되어 `자료 없음` 누적 보정이 동작하지 않는다.

## status

```bash
python scripts/simple_tasks.py status
```

항목 이름·크기·마감을 포함한 요약을 돌려준다. **사용자가 목록을 명시적으로 요청했을 때만 쓴다.** 카드 턴에서는 `load`의 개수만 쓴다.

## 그 외

```bash
python scripts/simple_tasks.py open-card "항목명"   # 카드 열림 기록
python scripts/simple_tasks.py close-card          # 카드 닫기
python scripts/simple_tasks.py end-session         # 세션 종료
```

`complete`와 `reject`는 내부에서 카드를 닫으므로 `close-card`를 따로 부르지 않아도 된다.

## 항목 그래프

항목은 서로 고립되어 있지 않다. 같은 파일을 열고, 같은 사람을 기다리고, 하나가 끝나야 다른 하나가 풀린다. 이 연결을 세 필드로 저장한다.

| 필드 | 뜻 | 어디서 오나 |
|---|---|---|
| `context` | 맥락 노드 — 파일·사람·도구 | 발화, `add --context` |
| `blocks` | 이 항목이 **막고 있는** 항목명들 | 발화, `add --blocks` |
| `spawned_by` | 이 항목이 나온 부모 항목 | `complete --new-idea`가 자동 기록 |

**엣지는 사용자가 발화한 것에서만 만든다.** 항목 이름이 비슷하다고 이어 붙이지 않는다. 거짓 `blocks` 하나가 생기면 그 항목은 조용히 후보에서 밀려나고 사용자는 이유를 알 수 없다. **놓친 엣지는 그냥 없는 것이지만 틀린 엣지는 항목을 죽인다.** 확신이 없으면 비워 둔다.

그래프 알고리즘을 돌리지 않는다. 항목이 수십 개인 그래프에서 중심성이나 군집을 계산하면 설명할 수 없는 카드가 나온다. 1-hop 조회와 개수 세기면 충분하다.

### 무엇이 달라지나

**차단이 이름을 갖는다.** `--blocking 4`는 "4개가 대기 중"까지밖에 못 쓰지만 `--blocks`는 무엇이 풀리는지 부를 수 있다. `이유` 줄이 사용자가 3초에 참·거짓을 판정할 수 있는 문장이 된다.

**따뜻한 맥락에 가점이 붙는다.** 직전에 완료한 항목과 `context`가 겹치면 `pick`이 40점을 더한다. 파일이 이미 열려 있고 사람 이름이 머리에 있어 **실제로 행동 비용이 낮기 때문**이지, 그 일이 더 중요해서가 아니다.

**수확이 공짜로 엣지를 만든다.** `--new-idea`로 들어온 항목은 `spawned_by`에 부모가 기록되고 부모의 `context`를 물려받는다. 사용자에게 아무것도 더 묻지 않는다.

### 출력 표면은 `이유` 줄 하나뿐

`pick`이 `why`를 돌려준다.

```json
"why": {
  "unblocks": ["김팀장 회신", "데이터 재수령"],
  "warm": ["보고서.docx"],
  "spawned_by": null
}
```

**그래프를 사용자에게 보여주지 않는다.** 연결 관계를 늘어놓는 것은 목록을 출력하는 것보다 나쁘다 — 훑을 거리에 이유까지 붙기 때문이다. `why`는 `이유` 한 줄로만 나가고, 여러 개가 차 있어도 **하나만** 고른다.

## 크기 보정

`거부 로그` 최근 10건을 스크립트가 자동으로 집계한다. 모델이 계산할 필요는 없고, `calibration` 필드를 읽어 반영만 한다.

- `너무 큼` 3회 이상 → `shrunk: true`, 기본 크기 한 단계 축소
- `이탈` 2회 이상 → 같은 조치
- 같은 항목에 `자료 없음` 2회 이상 → `items_needing_prep`에 오르고 후보에서 제외

**이 크기 보정이 우선순위 알고리즘보다 체감 품질을 좌우한다.** 그래서 사유 문자열을 정확히 넘기는 것이 중요하다.
