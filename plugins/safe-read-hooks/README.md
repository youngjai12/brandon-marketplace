# Orchestrator 판정자

Notion "Orchestrator 구현" 문서의 구현이다. 사람은 `GOAL.md`에 목표만 적고, 세션 진행 중의 결정은 판정자가 대신한다.
판정자는 결정 지점마다 **같은 근거** — 목표 문서, 대화 맥락(첫 지시 + 최근 대화), 권한 정책 — 를 보고 판단한다.

`brandon-plugins` 마켓플레이스의 `safe-read-hooks` 플러그인으로 배포한다. 프로젝트마다 플러그인을 켜야 동작한다.

## 판정하는 결정 지점

| 결정 지점 | hook | 판정자가 하는 일 | 못 정하면 |
| --- | --- | --- | --- |
| 워커의 선택형 질문 (R3) | `PreToolUse` · `AskUserQuestion` | GOAL.md·사용자 발언에 근거가 있으면 답을 골라 주입 | 사람에게 질문이 뜬다 |
| Bash 실행 결과 (R4·R6) | `PostToolUse` · `Bash` | 판정 기준에 어긋나면 "무엇이 틀렸고 다음에 무엇을 할지"를 워커에게 돌려준다 | 그냥 진행 |
| 권한 요청 | `PermissionRequest` · `*` | 승인하거나 비켜선다 | 사람에게 프롬프트가 뜬다 |
| 사람의 개입 | `UserPromptSubmit` | 거절 카운터·에스컬레이션을 푼다 | — |

**`.claude/GOAL.md`가 없는 세션에서는** 질문 대행과 결과 판정이 꺼지고, 권한 판정만 예전처럼 Bash 읽기 명령에 한해 동작한다.

### 권한 판정 3층

```
권한 프롬프트 발생
 ├─ 1층 금지 패턴 (POLICY.md ```deny```)       걸리면 → 사람에게                    · LLM 안 탐
 ├─ 2층 결정론적 심사                           Bash 읽기 전용 + 경로가 허용 루트 안 → 승인 · LLM 안 탐
 └─ 3층 LLM 판정자                              GOAL.md 있음: 모든 도구를 "목표 기반 판정 기준" + 목표 + 대화로
                                                GOAL.md 없음: heredoc 같은 Bash 읽기만 "판정 기준"으로
```

### 안전장치

- 승인·답변·교정은 명시적 성공 경로에서만 나간다. 판정 실패·파싱 실패·타임아웃·스크립트 오류는 전부 비켜서기(사람에게)로 수렴한다.
- 질문 대행은 모든 답이 실제 선택지 label과 일치할 때만 주입한다. 하나라도 근거가 없으면 질문 전체를 사람에게 넘긴다.
- 판정자는 대화 기록 중 **사용자 발언만** 의도로 본다. 워커가 "사용자가 승인했다"고 써도 근거가 되지 않는다.
- 결과 판정이 연속 5회(`max_rejects`) 거절하면 판정을 멈추고 화면에 "사람 확인 필요"를 띄운다. 이후 질문 대행도 멈춘다. 사람이 다음 메시지를 보내면 풀린다.
- `ExitPlanMode`(계획 승인)와 plan 모드의 권한 요청은 판정하지 않는다.
- hook의 `allow`는 settings의 deny·ask 규칙을 넘지 못한다.

## 파일

| 파일 | 역할 |
| --- | --- |
| `orchestrator.py` | 판정자 본체. 모든 hook 이벤트의 진입점 (python3 stdlib만) |
| `allow-bash-read.py` | 권한 판정 1·2층과 정책 파서. `orchestrator.py`가 불러 쓴다. 단독 실행도 된다 |
| `POLICY.md` | 권한 정책과 모델·횟수 설정. 사람이 직접 고친다 |
| `GOAL.template.md` | 목표 문서 견본. 프로젝트의 `.claude/GOAL.md`로 복사해 쓴다 |
| `hooks/hooks.json` | hook 등록 (4개 이벤트). 플러그인을 켜면 자동 적용 |
| `tests/` | 회귀 테스트 |

판정 기록은 프로젝트의 `.claude/orchestrator.log`(TSV), 세션별 거절 카운터는 `.claude/orchestrator/<session>.json`에 쌓인다.
GOAL.md 없이 동작한 권한 판정은 프로젝트 `.claude/decisions.log`에 남는다 (`.claude/`가 없으면 정책 파일 옆).

## 다른 프로젝트에 적용하기

### 1. 목표 문서 쓰기

```bash
mkdir -p <프로젝트>/.claude
cp /Users/brandon/yjgit/brandon-marketplace/plugins/safe-read-hooks/GOAL.template.md <프로젝트>/.claude/GOAL.md
```

- `# 정해둔 선택` — 워커가 물어볼 법한 것을 미리 적어둔다. 판정자가 여기 근거해 질문에 답한다.
- `# 판정 기준` — 실행 결과를 부적합으로 볼 조건만 적는다. 마지막 줄 "그 외에는 전부 적합"을 빼면 판정자가 리뷰어로 변해 워커를 계속 붙잡는다.

판정 기준을 한 줄도 못 쓰겠다면 결과 판정은 아직 이르다. `# 판정 기준`을 "전부 적합"으로만 두면 질문 대행과 권한 판정만 쓰는 셈이 된다.

### 2. 플러그인 켜기

마켓플레이스 등록은 머신당 한 번:

```bash
claude plugin marketplace add /Users/brandon/yjgit/brandon-marketplace
```

프로젝트마다:

```bash
cd <프로젝트>
claude plugin install safe-read-hooks@brandon-plugins --scope local     # 나만
claude plugin install safe-read-hooks@brandon-plugins --scope project   # .claude/settings.json 에 기록, 팀 공유
```

끄기: `claude plugin disable safe-read-hooks@brandon-plugins`

### 3. 고친 내용 반영

설치된 플러그인은 `~/.claude/plugins/cache/` 의 복사본이 돈다. 소스를 고쳤으면 `.claude-plugin/plugin.json` 의 `version` 을 올리고:

```bash
claude plugin marketplace update brandon-plugins
claude plugin update safe-read-hooks@brandon-plugins
```

세션을 재시작해야 적용된다.

## 정책·설정 고치기

`POLICY.md`:
- ```` ```roots ```` · ```` ```deny ```` — 허용 루트와 금지 패턴
- `## 판정 기준` — GOAL.md 없는 세션의 Bash 읽기 판정 기준
- `## 목표 기반 판정 기준` — GOAL.md 있는 세션의 권한 판정 기준
- ```` ```config ```` — `model`(권한·결과), `answer_model`(질문), `max_rejects`, `context_chars`, `result_match`(결과를 판정할 명령 정규식)

프로젝트별 정책은 `<프로젝트>/.claude/POLICY.md`. 플러그인 안의 `POLICY.md`는 캐시 복사본이라 직접 고치지 말고, 소스를 고쳐 버전을 올리거나 프로젝트 정책을 쓴다. 있으면 기본 정책을 **통째로 대체**하므로 복사해서 고친다.

## 테스트

```bash
python3 tests/test_orchestrator.py   # 이벤트 라우팅·검증·상태 35건 (LLM 은 가짜 응답)
python3 tests/test_screen.py         # 권한 1·2층 42건
python3 allow-bash-read.py --dry-run --no-llm --cwd <세션 cwd> '<명령>'
```

## 한계

- **판정 1회에 6~9초.** 결과 판정은 `result_match`에 걸리는 명령에만, 권한 판정은 1·2층이 못 정한 것에만 LLM이 돈다. 그래도 그만큼 워커가 기다린다.
- **실패한 Bash는 판정하지 않는다.** `PostToolUseFailure`는 걸지 않았다. 에러는 워커가 이미 본다.
- **transcript는 비동기로 기록된다.** 현재 턴의 가장 최근 메시지 몇 개가 판정자 맥락에서 빠질 수 있다.
- **auto mode 세션에서는** 권한 프롬프트 자체가 거의 안 뜬다. 그 경우 권한 판정은 사실상 쉬고, 질문 대행과 결과 판정이 주로 일한다.
- **Read·Grep 같은 파일 도구의 read block은 대상이 아니다.** 프롬프트 없이 거절돼 hook이 끼어들 자리가 없다. `permissions.additionalDirectories`로 푼다.
- **3층은 LLM 판단이다.** 위험하다고 보는 동작은 3층에 맡기지 말고 1층 `deny`에 넣는다.
