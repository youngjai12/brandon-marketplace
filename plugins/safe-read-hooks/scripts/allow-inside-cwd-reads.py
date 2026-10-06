#!/usr/bin/env python3
"""PreToolUse(Bash) hook: auto-allow read-only commands whose every path
(after following `cd`) resolves inside the session cwd / project dir.
Anything it can't prove safe falls through to the normal permission prompt."""
import json, os, re, shlex, sys

READ_CMDS = {"cat", "head", "tail", "ls", "grep", "egrep", "fgrep", "rg", "find",
             "wc", "file", "stat", "tree", "du", "echo", "pwd", "cd", "sort",
             "uniq", "cut", "tr", "basename", "dirname", "realpath", "true"}
FIND_BAD = {"-exec", "-execdir", "-ok", "-okdir", "-delete", "-fprint", "-fprint0", "-fprintf", "-fls"}
SEPS = {";", "&&", "||", "|", "&"}


def fallthrough():
    sys.exit(0)


def inside(path, roots):
    if path == "/dev/null":
        return True
    rp = os.path.realpath(path)
    return any(rp == r or rp.startswith(r + os.sep) for r in roots)


def path_of(tok, cwd):
    tok = os.path.expanduser(tok)
    m = re.search(r"[*?\[]", tok)
    if m:  # glob: check the literal directory prefix
        tok = os.path.dirname(tok[:m.start()]) or "."
    return os.path.join(cwd, tok)


def main():
    data = json.load(sys.stdin)
    if data.get("tool_name") != "Bash":
        fallthrough()
    cmd = data.get("tool_input", {}).get("command", "")
    cwd = data.get("cwd") or os.getcwd()
    roots = {os.path.realpath(cwd)}
    if os.environ.get("CLAUDE_PROJECT_DIR"):
        roots.add(os.path.realpath(os.environ["CLAUDE_PROJECT_DIR"]))

    # no expansions / substitutions / heredocs / subshells
    if re.search(r"\$[({A-Za-z_]|`|<<|<\(|>\(", cmd):
        fallthrough()

    lex = shlex.shlex(cmd.replace("\n", " ; "), posix=True, punctuation_chars=";&|<>()")
    lex.whitespace_split = True
    try:
        toks = list(lex)
    except ValueError:
        fallthrough()

    cur = cwd
    seg = []

    def check_segment(seg):
        nonlocal cur
        if not seg:
            return
        name = os.path.basename(seg[0])
        if name not in READ_CMDS:
            fallthrough()
        args = seg[1:]
        if name == "find" and any(a in FIND_BAD for a in args):
            fallthrough()
        if name == "rg" and any(a.startswith("--pre") for a in args):
            fallthrough()
        if name == "cd":
            target = os.path.expanduser(args[0]) if args else os.path.expanduser("~")
            cur = os.path.normpath(os.path.join(cur, target))
            if not inside(cur, roots):
                fallthrough()
            return
        if name == "echo":
            return
        for a in args:
            if a.startswith("-"):
                if "=" in a:
                    a = a.split("=", 1)[1]
                else:
                    continue
            if not inside(path_of(a, cur), roots):
                fallthrough()

    i = 0
    while i < len(toks):
        t = toks[i]
        if t in SEPS:
            check_segment(seg); seg = []
        elif t in ("(", ")"):
            fallthrough()
        elif t in (">", ">>", "&>", "&>>", ">|"):
            nxt = toks[i + 1] if i + 1 < len(toks) else ""
            if nxt != "/dev/null":
                fallthrough()
            if seg and seg[-1].isdigit():
                seg.pop()
            i += 1
        elif t == ">&":
            if seg and seg[-1].isdigit():
                seg.pop()
            i += 1  # 2>&1
        elif t == "<":
            nxt = toks[i + 1] if i + 1 < len(toks) else ""
            if not inside(path_of(nxt, cur), roots):
                fallthrough()
            i += 1
        elif set(t) <= set(";&|<>()"):
            fallthrough()  # unknown operator
        else:
            seg.append(t)
        i += 1
    check_segment(seg)

    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "allow",
        "permissionDecisionReason": "read-only command; all paths inside working directory",
    }}))


if __name__ == "__main__":
    main()
