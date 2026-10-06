# Bash 권한 자동승인 정책

이 파일이 판정의 유일한 근거다. 권한 프롬프트가 뜰 때마다 새로 읽으므로, 고치면 다음 프롬프트부터 바로 적용된다.
정책 파일은 이 순서로 찾는다. 하나도 없으면 판정자는 개입하지 않고 모든 프롬프트가 평소대로 사람에게 온다.
  1. 환경변수 `ORCH_POLICY` 가 가리키는 파일 — 지정했는데 파일이 없으면 폴백하지 않고 비켜선다
  2. 세션 워킹디렉토리의 `.claude/POLICY.md` — 프로젝트별 정책
  3. 판정 스크립트 옆의 이 파일 — 기본 정책
이 파일 이름을 바꾸는 것이 곧 기본 정책의 off 스위치다.

판정자는 **승인하거나 비켜서기만** 한다. 거절은 하지 않는다.
좁게 쓰면 사람에게 묻는 횟수가 늘어날 뿐이고, 넓게 쓰면 신뢰 경계가 그만큼 넓어진다.

## 허용 루트

읽기를 자동승인할 경로. 하위 전체를 포함한다. 한 줄에 하나. `~`는 홈으로 전개된다.
세션의 워킹디렉토리는 적지 않아도 항상 허용된다.

```roots
/private/tmp/claude-501
/Users/brandon/yjgit
~/.claude/plugins
~/.claude/projects
```

## 절대 금지

이 패턴 중 하나라도 명령에 걸리면 판정 없이 즉시 사람에게 넘긴다. LLM도 타지 않는다.
Python 정규식(`re.search`), 한 줄에 하나. `\b`는 단어 경계다.

```deny
\bsudo\b
\brm\b
\b(curl|wget|nc|ncat|ssh|scp|rsync|ftp|telnet)\b
\b(chmod|chown|ln|mkfs|dd|shutdown|reboot|kill|killall|pkill|launchctl|crontab)\b
\bgit\s+(commit|push|reset|clean|checkout|rebase|merge|stash|restore|switch)\b
\b(pip|pip3|npm|pnpm|yarn|brew|uv|cargo|gem|apt|apt-get)\s+(install|uninstall|remove|add|publish)\b
\b(tee|xargs|eval|exec|source)\b
\.ssh/|\.aws/|\.gnupg/|\.netrc|id_rsa|id_ed25519|\.credentials\.json|(^|[\s/])\.env\b
```

## 설정

```config
model: sonnet
answer_model: haiku
timeout: 45
max_rejects: 5
context_chars: 6000
result_match: (^|[\s/;&|(])(python3?|pytest|uv\s+run|node|npm\s+run|pnpm\s+run|make|Rscript|dbt)(\s|$)
```

- `model` — 권한 판정과 실행 결과 판정에 쓰는 모델. `answer_model` — 질문 대행에 쓰는 모델 (선택지가 주어져 있어 가벼운 모델로 충분)
- `max_rejects` — 실행 결과 판정에서 연속 거절이 이 횟수에 닿으면 판정을 멈추고 사람을 부른다. 사람이 다음 메시지를 보내면 풀린다
- `context_chars` — 판정자에게 넘기는 최근 대화 분량
- `result_match` — 실행 결과를 판정할 Bash 명령의 정규식. 여기 안 걸리는 명령(ls, grep, git status …)은 판정하지 않는다

## 판정 기준

결정론적 심사로 판정이 안 되는 명령만 — heredoc으로 코드를 넘기거나, 명령 치환이 섞여 정적 분석이 안 되는 경우 —
여기 기준으로 LLM 판정자에게 넘어간다. 이 절의 문장 전체가 판정자에게 그대로 전달된다.

- 파일을 읽어 화면에 출력하기만 하는 명령은 승인한다.
- 파일을 새로 만들거나, 기존 파일의 내용·권한·위치를 바꾸거나, 지우면 승인하지 않는다.
  `/dev/null`로 버리는 리다이렉트는 쓰기로 보지 않는다.
- 네트워크로 나가는 동작이 있으면 승인하지 않는다.
- 읽는 경로가 위 "허용 루트" 밖이면 승인하지 않는다. 세션 워킹디렉토리 안은 허용 루트로 본다.
- 인라인 코드(python heredoc 등)는 그 코드가 실제로 무엇을 하는지 보고 판정한다.
  읽기와 출력만 하면 승인하고, 쓰기·삭제·네트워크·서브프로세스 실행이 하나라도 있으면 승인하지 않는다.
- 판단이 서지 않으면 승인하지 않는다. 승인하지 않으면 사람에게 넘어가므로 그쪽이 안전한 기본값이다.

## 목표 기반 판정 기준

세션 워킹디렉토리에 `.claude/GOAL.md` 가 있을 때만 쓰인다. 그 세션에서는 Bash 뿐 아니라 모든 도구(Edit, Write, MCP 도구 등)의
권한 요청이 판정 대상이 되고, 1층 금지 패턴과 2층 결정론적 승인을 통과하지 못한 요청이 여기 기준으로 LLM 판정자에게 간다.
판정자는 이 절과 함께 GOAL.md 와 최근 대화를 받는다. 이 절의 문장 전체가 그대로 전달된다.

- 위 "판정 기준"으로 승인되는 읽기는 승인한다.
- 세션 워킹디렉토리 안의 파일을 만들거나 고치는 것은, 대화 맥락상 GOAL.md 의 목표를 이루는 데 필요한 작업이면 승인한다.
- 워킹디렉토리 안에서 테스트·빌드·분석 스크립트를 실행하는 것은, 목표를 위한 것이면 승인한다.
- 다음이 하나라도 있으면 승인하지 않는다: 네트워크로 나가거나 외부 서비스에 쓰는 동작, 워킹디렉토리 밖에 쓰기,
  파일 삭제, git 기록을 바꾸는 동작, 패키지 설치, 권한·설정 파일 변경.
- 사용자가 대화에서 하지 말라고 한 작업, GOAL.md 범위 밖의 작업은 승인하지 않는다.
- 판단이 서지 않으면 승인하지 않는다.
