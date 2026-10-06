#!/usr/bin/env python3
"""PermissionRequest hook: Bash 읽기 명령을 POLICY.md 근거로 자동승인한다.

판정은 세 층으로 내려간다.
  1. 금지 패턴        POLICY.md 의 ```deny``` 에 걸리면 즉시 사람에게 (LLM 안 탐)
  2. 결정론적 심사     읽기 전용 프로그램 + 쓰기 리다이렉트 없음 + 경로가 허용 루트 안 → 즉시 승인 (LLM 안 탐)
  3. LLM 판정자        정적 분석이 불가능한 명령(heredoc, 명령 치환, 인터프리터)만 POLICY.md 전문을 근거로 판정

승인하거나 비켜서기만 한다. 거절은 하지 않는다. 비켜서면(exit 0, 출력 없음) 평소대로 사람에게 프롬프트가 뜬다.
모든 실패 경로 — 정책 파일 없음, 파싱 실패, 판정자 오류, 타임아웃 — 는 비켜서기로 수렴한다.
"""

import json
import os
import re
import shlex
import subprocess
import sys
import time

# 쓰기 능력이 없는 프로그램만. 인터프리터는 일부러 뺐다 (3층으로 보낸다).
READONLY = {
    "cat", "head", "tail", "grep", "egrep", "fgrep", "rg", "ls", "wc", "find",
    "sed", "awk", "jq", "yq", "sort", "uniq", "cut", "tr", "nl", "od", "xxd",
    "strings", "diff", "comm", "stat", "file", "du", "df", "which", "type",
    "basename", "dirname", "realpath", "echo", "printf", "pwd", "true", "date",
    "tree", "column", "cmp", "md5", "shasum", "uname", "seq",
}

# 정적 분석이 불가능하다 → LLM 판정자에게
INTERPRETERS = {"python", "python3", "node", "ruby", "perl", "bash", "sh", "zsh", "osascript"}

# 이후 상대경로의 기준을 바꾼다 → 정적으로 경로를 못 따라간다 → LLM 판정자에게
DIR_CHANGERS = {"cd", "pushd", "popd"}

# /dev/null 로 버리는 리다이렉트는 쓰기로 보지 않는다
NULL_REDIRECT = re.compile(r"(?:\d*>>?|&>>?)\s*/dev/null|\d*>&\d")
SEP_CHARS = "();|&\n"
ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
VAR_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")


class Aside(Exception):
    """사람에게 넘긴다."""

    def __init__(self, reason):
        self.reason = reason


class NeedsJudge(Exception):
    """정적으로 못 읽는다. LLM 판정자에게 넘긴다."""

    def __init__(self, reason):
        self.reason = reason


# ---------------------------------------------------------------- policy

def find_policy(cwd):
    explicit = os.environ.get("ORCH_POLICY")
    if explicit:
        # 명시적으로 가리킨 파일이 없으면 폴백하지 않고 비켜선다
        return explicit if os.path.isfile(explicit) else None
    for cand in (
        os.path.join(cwd, ".claude", "POLICY.md") if cwd else None,
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "POLICY.md"),
    ):
        if cand and os.path.isfile(cand):
            return cand
    return None


def fenced(text, tag):
    """```<tag> ... ``` 블록의 내용 줄들."""
    out, inside = [], False
    for line in text.splitlines():
        s = line.strip()
        if not inside and s == "```" + tag:
            inside = True
            continue
        if inside:
            if s.startswith("```"):
                break
            if s and not s.startswith("#"):
                out.append(s)
    return out


def section(text, heading):
    """## <heading> 아래부터 다음 ## 까지."""
    out, inside = [], False
    for line in text.splitlines():
        if line.startswith("## "):
            inside = line[3:].strip() == heading
            continue
        if inside:
            out.append(line)
    return "\n".join(out).strip()


def load_policy(path):
    text = open(path, encoding="utf-8").read()
    cfg = {}
    for line in fenced(text, "config"):
        if ":" in line:
            k, v = line.split(":", 1)
            cfg[k.strip()] = v.strip()
    roots = []
    for r in fenced(text, "roots"):
        roots.append(os.path.realpath(os.path.expanduser(r)))
    return {
        "path": path,
        "roots": roots,
        "deny": fenced(text, "deny"),
        "criteria": section(text, "판정 기준") or section(text, "Criteria"),
        "goal_criteria": section(text, "목표 기반 판정 기준"),
        "model": cfg.get("model", "sonnet"),
        "timeout": int(cfg.get("timeout", "45")),
        "config": cfg,
    }


# ---------------------------------------------------------------- layer 1 + 2

def check_deny(cmd, deny):
    for pat in deny:
        try:
            if re.search(pat, cmd):
                raise Aside("금지 패턴: %s" % pat)
        except re.error:
            continue  # 정책 파일의 잘못된 정규식은 무시하고 다음 패턴으로


def under(path, roots):
    p = os.path.realpath(os.path.expanduser(path))
    for r in roots:
        if p == r or p.startswith(r.rstrip("/") + "/"):
            return True
    return False


def resolve(tok, assigns):
    """같은 명령 안에서 선언된 VAR 을 전개한다. 못 풀면 NeedsJudge."""
    def sub(m):
        name = m.group(1) or m.group(2)
        if name not in assigns:
            raise NeedsJudge("전개 못 한 변수 $%s" % name)
        return assigns[name]

    return VAR_REF.sub(sub, tok)


def check_flags(prog, args):
    if prog == "sed":
        for a in args:
            if re.match(r"^-[a-zA-Z]*i", a):
                raise Aside("sed 제자리 편집 플래그: %s" % a)
            if not a.startswith("-") and re.search(r"(^|[;}\n])\s*[0-9,$/^]*\s*[wWe](\s|$)", a):
                raise Aside("sed w/W/e 커맨드(쓰기·실행)")
    if prog == "find":
        bad = {"-delete", "-exec", "-execdir", "-ok", "-okdir",
               "-fprint", "-fprintf", "-fls", "-printf"}
        for a in args:
            if a in bad:
                raise Aside("find 실행/쓰기 플래그: %s" % a)
    if prog == "awk":
        for a in args:
            if "system(" in a or "close(" in a or "|" in a or "print >" in a:
                raise Aside("awk 실행/쓰기 구문")
    positional = [a for a in args if not a.startswith("-")]
    if prog in ("uniq", "xxd") and len(positional) >= 2:
        raise Aside("%s 두 번째 인자는 출력 파일" % prog)
    if prog == "rg" and any(a == "--pre" or a.startswith("--pre=") for a in args):
        raise Aside("rg --pre 는 명령을 실행한다")
    if prog == "yq" and any(re.match(r"^-[a-zA-Z]*i", a) or a == "--inplace" for a in args):
        raise Aside("yq 제자리 편집")
    if prog == "date" and any(a == "-s" or a.startswith("--set") for a in args):
        raise Aside("date 시각 변경")
    if prog == "tree" and "-o" in args:
        raise Aside("tree 출력 파일 플래그")
    if prog in ("sort", "shasum", "md5"):
        for a in args:
            if a in ("-o", "--output") or a.startswith("--output="):
                raise Aside("%s 출력 파일 플래그" % prog)


def screen(cmd, cwd, roots):
    """결정론적 심사. 통과하면 (True, 근거). 아니면 Aside/NeedsJudge 를 던진다."""
    for marker, why in (("<", "리다이렉트/heredoc"), ("`", "백틱 명령 치환"), ("$(", "명령 치환")):
        if marker in cmd:
            raise NeedsJudge("정적 분석 불가: %s" % why)

    stripped = NULL_REDIRECT.sub(" ", cmd)
    if ">" in stripped:
        raise Aside("쓰기 리다이렉트")

    try:
        lx = shlex.shlex(stripped, posix=True, punctuation_chars=SEP_CHARS)
        lx.whitespace = " \t\r"   # 개행은 공백이 아니라 구분자다
        lx.whitespace_split = True
        lx.commenters = ""          # a#b 에서 # 뒤를 버리면 뒤따르는 명령이 가려진다
        tokens = list(lx)
    except ValueError as e:
        raise NeedsJudge("토크나이즈 실패: %s" % e)

    segments, cur = [], []
    for t in tokens:
        if t and all(c in SEP_CHARS for c in t):
            segments.append(cur)
            cur = []
        else:
            cur.append(t)
    segments.append(cur)

    assigns, checked = {}, []
    for seg in segments:
        i = 0
        while i < len(seg) and ASSIGN.match(seg[i]):
            k, v = seg[i].split("=", 1)
            assigns[k] = resolve(v, assigns)
            i += 1
        rest = seg[i:]
        if not rest:
            continue  # 순수 변수 선언 구간

        prog = os.path.basename(rest[0])
        args = rest[1:]
        if prog in INTERPRETERS:
            raise NeedsJudge("인터프리터 실행: %s" % prog)
        if prog in DIR_CHANGERS:
            raise NeedsJudge("디렉토리 이동 후 상대경로: %s" % prog)
        if prog not in READONLY:
            raise Aside("읽기 전용 목록에 없는 프로그램: %s" % prog)
        check_flags(prog, args)

        for a in args:
            if a.startswith("-"):
                continue
            a = resolve(a, assigns)
            if "/" not in a and not a.startswith("~"):
                continue  # cwd 안의 이름이거나 패턴. 워킹디렉토리는 항상 허용
            p = os.path.expanduser(a)
            if not os.path.isabs(p):
                p = os.path.join(cwd or os.getcwd(), p)
            if not under(p, roots):
                raise Aside("허용 루트 밖 경로: %s" % p)
            checked.append(p)

    return True, "읽기 전용 · 경로 %d건 루트 안" % len(checked)


# ---------------------------------------------------------------- layer 3

JUDGE_PROMPT = """너는 Claude Code 세션의 Bash 권한 요청을 판정한다.
아래 정책 문서의 "판정 기준"만이 근거다. 코드 품질·스타일·개선점은 절대 언급하지 마라.

=== 정책: 판정 기준 ===
{criteria}

=== 정책: 허용 루트 (하위 전체 포함) ===
{roots}

=== 세션 워킹디렉토리 (허용 루트로 본다) ===
{cwd}

=== 판정할 Bash 명령 ===
{cmd}

이 명령을 사람 대신 자동승인해도 되는지만 판정하라.
JSON 하나만 출력하고 다른 말은 하지 마라.
승인: {{"allow": true, "reason": "한 문장"}}
비승인: {{"allow": false, "reason": "한 문장"}}
판단이 서지 않으면 반드시 {{"allow": false, ...}} 를 출력하라."""


def ask_judge(cmd, cwd, pol):
    prompt = JUDGE_PROMPT.format(
        criteria=pol["criteria"] or "(판정 기준이 비어 있다 — 승인하지 마라)",
        roots="\n".join(pol["roots"]) or "(없음)",
        cwd=cwd or "(모름)",
        cmd=cmd,
    )
    env = dict(os.environ, ORCH_JUDGE="1")
    try:
        proc = subprocess.run(
            ["claude", "-p", prompt,
             "--model", pol["model"],
             "--allowedTools", "",
             "--permission-prompts", "none",
             "--output-format", "json",
             "--settings", '{"disableAllHooks":true}'],
            capture_output=True, text=True, env=env, timeout=pol["timeout"],
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        raise Aside("판정자 호출 실패: %s" % type(e).__name__)

    if proc.returncode != 0:
        raise Aside("판정자 종료코드 %d" % proc.returncode)
    try:
        result = json.loads(proc.stdout).get("result", "")
    except (ValueError, AttributeError):
        raise Aside("판정자 응답 파싱 실패")

    m = re.search(r"\{.*\}", result, re.S)
    if not m:
        raise Aside("판정자 응답에 JSON 없음")
    try:
        verdict = json.loads(m.group(0))
    except ValueError:
        raise Aside("판정자 JSON 파싱 실패")

    if verdict.get("allow") is not True:
        raise Aside("판정자 비승인: %s" % verdict.get("reason", "이유 없음"))
    return "판정자 승인: %s" % verdict.get("reason", "")


# ---------------------------------------------------------------- decide / io

def decide(cmd, cwd, pol, use_llm=True):
    """(allowed, layer, reason) 를 돌려준다. 예외를 밖으로 내지 않는다."""
    try:
        check_deny(cmd, pol["deny"])
    except Aside as a:
        return False, "deny", a.reason
    try:
        screen(cmd, cwd, pol["roots"])
        return True, "static", "읽기 전용 · 경로 전부 허용 루트 안"
    except Aside as a:
        return False, "static", a.reason
    except NeedsJudge as n:
        if not use_llm:
            return False, "judge", "%s (LLM 생략)" % n.reason
        try:
            return True, "judge", ask_judge(cmd, cwd, pol)
        except Aside as a:
            return False, "judge", "%s / %s" % (n.reason, a.reason)


def log(pol, sid, layer, allowed, cmd, reason, cwd=None):
    # 플러그인으로 설치되면 정책 파일이 캐시에 있으므로, 프로젝트 .claude/ 가 있으면 그쪽에 쓴다
    proj = os.path.join(cwd, ".claude") if cwd else ""
    base = proj if proj and os.path.isdir(proj) else os.path.dirname(pol["path"])
    path = os.path.join(base, "decisions.log")
    row = "\t".join([
        time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        (sid or "-")[:8], layer,
        "ALLOW" if allowed else "ASIDE",
        cmd.replace("\n", "\\n")[:300],
        reason.replace("\n", " ")[:200],
    ])
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(row + "\n")
    except OSError:
        pass


def main():
    if "--dry-run" in sys.argv:
        argv = [a for a in sys.argv[1:] if a != "--dry-run"]
        use_llm = "--no-llm" not in argv
        argv = [a for a in argv if a != "--no-llm"]
        cwd = os.getcwd()
        if "--cwd" in argv:
            i = argv.index("--cwd")
            cwd = argv[i + 1]
            del argv[i:i + 2]
        cmd = argv[0] if argv else sys.stdin.read()
        pol_path = find_policy(cwd)
        if not pol_path:
            print("정책 파일 없음 → 개입하지 않음")
            return 0
        pol = load_policy(pol_path)
        allowed, layer, reason = decide(cmd, cwd, pol, use_llm=use_llm)
        print("정책   : %s" % pol["path"])
        print("층     : %s" % {"deny": "1 금지 패턴", "static": "2 결정론적 심사",
                               "judge": "3 LLM 판정자"}[layer])
        print("결과   : %s" % ("자동승인" if allowed else "사람에게 넘김"))
        print("근거   : %s" % reason)
        return 0

    # hook 모드
    if os.environ.get("ORCH_JUDGE"):
        return 0  # 판정자 자신의 세션 — 재귀 차단
    try:
        ev = json.loads(sys.stdin.read())
    except ValueError:
        return 0
    if ev.get("tool_name") != "Bash":
        return 0
    cmd = (ev.get("tool_input") or {}).get("command") or ""
    if not cmd.strip():
        return 0
    cwd = ev.get("cwd") or ""
    pol_path = find_policy(cwd)
    if not pol_path:
        return 0
    try:
        pol = load_policy(pol_path)
    except OSError:
        return 0

    allowed, layer, reason = decide(cmd, cwd, pol)
    log(pol, ev.get("session_id"), layer, allowed, cmd, reason, cwd)
    if not allowed:
        return 0  # 비켜선다 → 평소대로 사람에게 프롬프트

    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PermissionRequest",
            "decision": {"behavior": "allow", "message": reason},
        },
        "systemMessage": "자동승인(%s): %s" % (layer, reason),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
