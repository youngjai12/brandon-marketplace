"""orchestrator.py 의 이벤트 라우팅·검증·상태 회귀 테스트. LLM 은 ORCH_FAKE_LLM 으로 대체한다.

python3 tests/test_orchestrator.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ORCH = os.path.join(HERE, "..", "orchestrator.py")
TEMPLATE = os.path.join(HERE, "..", "GOAL.template.md")

QS = [
    {"question": "어느 지표부터?", "header": "지표", "multiSelect": False,
     "options": [{"label": "샤프"}, {"label": "MDD"}]},
]
QS_MULTI = [
    {"question": "어떤 산출물?", "header": "산출물", "multiSelect": True,
     "options": [{"label": "수익률 표"}, {"label": "샤프"}, {"label": "MDD"}]},
]


def make_project(with_goal=True):
    d = tempfile.mkdtemp(prefix="orch-test-")
    os.makedirs(os.path.join(d, ".claude"))
    if with_goal:
        shutil.copy(TEMPLATE, os.path.join(d, ".claude", "GOAL.md"))
    tr = os.path.join(d, "transcript.jsonl")
    with open(tr, "w", encoding="utf-8") as f:
        f.write(json.dumps({"type": "user", "message": {"role": "user", "content": "모멘텀 백테스트 해줘"}}) + "\n")
        f.write(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "데이터부터 보겠습니다"},
            {"type": "tool_use", "name": "Bash", "input": {"command": "ls data"}}]}}) + "\n")
        f.write(json.dumps({"type": "user", "isSidechain": True, "message": {"role": "user", "content": "서브에이전트 잡음"}}) + "\n")
    return d, tr


def run(ev, fake=None, extra_env=None):
    """(출력 dict 또는 None, LLM 호출 횟수)"""
    calls = tempfile.mktemp(prefix="orch-calls-")
    env = {k: v for k, v in os.environ.items() if not k.startswith("ORCH_")}
    if fake is not None:
        env["ORCH_FAKE_LLM"] = fake
        env["ORCH_FAKE_LLM_LOG"] = calls
    env.update(extra_env or {})
    p = subprocess.run([sys.executable, ORCH], input=json.dumps(ev), capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stderr
    n = sum(1 for _ in open(calls)) if os.path.exists(calls) else 0
    prompt = json.loads(open(calls).readline())["prompt"] if n else ""
    return (json.loads(p.stdout) if p.stdout.strip() else None), n, prompt


def ev(cwd, tr, event, tool=None, inp=None, **kw):
    e = {"session_id": "sess-1234abcd", "cwd": cwd, "transcript_path": tr,
         "hook_event_name": event, "permission_mode": "default"}
    if tool:
        e["tool_name"] = tool
        e["tool_input"] = inp or {}
    e.update(kw)
    return e


results = []


def check(name, cond, detail=""):
    results.append(cond)
    print("%s  %s%s" % ("ok  " if cond else "FAIL", name, "" if cond else "  — %s" % detail))


P, TR = make_project()
NOGOAL, TR2 = make_project(with_goal=False)
OK_ANS = json.dumps({"answers": {"어느 지표부터?": "샤프"}, "reason": "GOAL.md 지표 우선순위"}, ensure_ascii=False)

# ── R3 질문 대행
q = ev(P, TR, "PreToolUse", "AskUserQuestion", {"questions": QS})
out, n, prompt = run(q, OK_ANS)
check("질문: 유효한 답 주입", out and out["hookSpecificOutput"]["permissionDecision"] == "allow"
      and out["hookSpecificOutput"]["updatedInput"]["answers"] == {"어느 지표부터?": "샤프"}
      and out["hookSpecificOutput"]["updatedInput"]["questions"] == QS, out)
check("질문: 판정자가 GOAL·첫 지시·최근 대화를 받음",
      "샤프 > MDD" in prompt and "[첫 지시] 사용자: 모멘텀 백테스트 해줘" in prompt and "Claude 도구호출 Bash" in prompt, prompt[:300])
check("질문: sidechain 대화는 제외", "서브에이전트 잡음" not in prompt)
out, n, _ = run(ev(NOGOAL, TR2, "PreToolUse", "AskUserQuestion", {"questions": QS}), OK_ANS)
check("질문: GOAL.md 없으면 개입 안 함 · LLM 안 탐", out is None and n == 0, (out, n))
out, _, _ = run(q, json.dumps({"answers": {"어느 지표부터?": "CAGR"}}, ensure_ascii=False))
check("질문: 없는 label 이면 사람에게", out is None, out)
out, _, _ = run(q, json.dumps({"answers": {}}))
check("질문: 빈 답이면 사람에게", out is None, out)
out, _, _ = run(q, "모르겠습니다")
check("질문: 판정자 응답이 JSON 아니면 사람에게", out is None, out)
qm = ev(P, TR, "PreToolUse", "AskUserQuestion", {"questions": QS_MULTI})
out, _, _ = run(qm, json.dumps({"answers": {"어떤 산출물?": "샤프, MDD"}}, ensure_ascii=False))
check("질문: multiSelect 쉼표 결합 허용", out and out["hookSpecificOutput"]["updatedInput"]["answers"]["어떤 산출물?"] == "샤프, MDD", out)
out, _, _ = run(qm, json.dumps({"answers": {"어떤 산출물?": "샤프, 알파"}}, ensure_ascii=False))
check("질문: multiSelect 중 하나라도 틀리면 사람에게", out is None, out)
out, _, _ = run(ev(P, TR, "PreToolUse", "AskUserQuestion", {"questions": QS + QS_MULTI}), OK_ANS)
check("질문: 일부만 답하면 사람에게", out is None, out)

# ── R4 실행 결과 판정 + R6
def bash_result(cmd, stdout="done"):
    return ev(P, TR, "PostToolUse", "Bash", {"command": cmd},
              tool_response={"stdout": stdout, "stderr": "", "interrupted": False, "isImage": False})

BAD = json.dumps({"ok": False, "reason": "일별 리밸런싱이다. 월별로 바꿔라"}, ensure_ascii=False)
out, n, _ = run(bash_result("ls data"), BAD)
check("결과: result_match 밖 명령은 LLM 안 탐", out is None and n == 0, (out, n))
out, n, prompt = run(bash_result("python3 backtest.py --freq D", "freq=D sharpe=1.2"), json.dumps({"ok": True}))
check("결과: 적합이면 개입 안 함", out is None and n == 1, (out, n))
check("결과: 판정자가 명령과 출력을 받음", "backtest.py --freq D" in prompt and "sharpe=1.2" in prompt)
out, _, _ = run(bash_result("python3 backtest.py --freq D"), BAD)
check("결과: 부적합이면 block + reason", out and out["decision"] == "block" and "월별" in out["reason"], out)
check("결과: 화면에 교정 횟수 표시", out and "(1/5)" in out["systemMessage"], out)
state = os.path.join(P, ".claude", "orchestrator", "sess-1234abcd.json")
run(bash_result("python3 backtest.py"), json.dumps({"ok": True}))
check("결과: 적합 판정이 나오면 카운터 초기화", json.load(open(state))["rejects"] == 0)
for _ in range(4):
    run(bash_result("python3 backtest.py"), BAD)
out, _, _ = run(bash_result("python3 backtest.py"), BAD)
check("결과: 5회째 에스컬레이션", out and "사람 확인 필요" in out["systemMessage"] and json.load(open(state))["escalated"], out)
out, n, _ = run(bash_result("python3 backtest.py"), BAD)
check("결과: 에스컬레이션 뒤엔 판정 중단", out is None and n == 0, (out, n))
out, n, _ = run(q, OK_ANS)
check("질문: 에스컬레이션 뒤엔 사람에게", out is None and n == 0, (out, n))
run(ev(P, TR, "UserPromptSubmit", prompt="계속해"))
check("사람 입력이 에스컬레이션 해제", json.load(open(state)) == {"rejects": 0, "escalated": False})
out, n, _ = run(bash_result("python3 backtest.py"), BAD)
check("해제 뒤 판정 재개", out is not None and n == 1, (out, n))
run(ev(P, TR, "UserPromptSubmit", prompt="ok"))

# ── 권한 판정
ALLOW = json.dumps({"allow": True, "reason": "목표 작업 파일 수정"}, ensure_ascii=False)
edit = ev(P, TR, "PermissionRequest", "Edit", {"file_path": os.path.join(P, "backtest.py"), "old_string": "D", "new_string": "M"})
out, n, prompt = run(edit, ALLOW)
check("권한: GOAL 있으면 Edit 도 판정", out and out["hookSpecificOutput"]["decision"]["behavior"] == "allow", out)
check("권한: 목표 기반 판정 기준을 받음", "워킹디렉토리 안의 파일을 만들거나 고치는" in prompt)
out, n, _ = run(edit, json.dumps({"allow": False, "reason": "범위 밖"}, ensure_ascii=False))
check("권한: 비승인이면 사람에게", out is None, out)
out, n, _ = run(ev(NOGOAL, TR2, "PermissionRequest", "Edit", {"file_path": os.path.join(NOGOAL, "a.py")}), ALLOW)
check("권한: GOAL 없으면 Edit 은 개입 안 함", out is None and n == 0, (out, n))
out, n, _ = run(ev(NOGOAL, TR2, "PermissionRequest", "Bash", {"command": "cat %s/x" % HERE}), ALLOW)
check("권한: GOAL 없어도 기존 Bash 읽기 판정 유지", out and out["systemMessage"].startswith("자동승인(static)"), out)
out, n, _ = run(ev(P, TR, "PermissionRequest", "Bash", {"command": "rm -rf build"}), ALLOW)
check("권한: 금지 패턴은 LLM 안 타고 사람에게", out is None and n == 0, (out, n))
out, n, _ = run(ev(P, TR, "PermissionRequest", "Write", {"file_path": os.path.join(P, ".env"), "content": "x"}), ALLOW)
check("권한: 비-Bash 도구도 경로에 금지 패턴 적용", out is None and n == 0, (out, n))
out, n, _ = run(ev(P, TR, "PermissionRequest", "Bash", {"command": "ls %s" % HERE}), ALLOW)
check("권한: 결정론적 읽기는 LLM 없이 승인", out and n == 0 and "static" in out["systemMessage"], (out, n))
out, n, prompt = run(ev(P, TR, "PermissionRequest", "Bash", {"command": "pytest -q"}), ALLOW)
check("권한: 2층이 못 정한 Bash 는 메모와 함께 판정자에게", out and n == 1 and "읽기 전용 목록에 없는 프로그램: pytest" in prompt, (out, n))
out, n, _ = run(ev(P, TR, "PermissionRequest", "ExitPlanMode", {"plan": "x"}), ALLOW)
check("권한: ExitPlanMode 는 항상 사람에게", out is None and n == 0, (out, n))
out, n, _ = run(dict(edit, permission_mode="plan"), ALLOW)
check("권한: plan 모드에선 개입 안 함", out is None and n == 0, (out, n))

# ── 공통 안전장치
out, n, _ = run(q, OK_ANS, {"ORCH_JUDGE": "1"})
check("재귀 차단: 판정자 세션에선 아무것도 안 함", out is None and n == 0, (out, n))
p = subprocess.run([sys.executable, ORCH], input="not json", capture_output=True, text=True)
check("깨진 입력은 조용히 통과", p.returncode == 0 and not p.stdout.strip())
check("판정 로그 기록", any("\tANSWER\t" in l for l in open(os.path.join(P, ".claude", "orchestrator.log"))))

shutil.rmtree(P)
shutil.rmtree(NOGOAL)
print("\n%d/%d 통과" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
