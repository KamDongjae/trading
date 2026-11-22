# -*- coding: utf-8 -*-
"""
[2026-11-22 수동, 사용자 요청] 1회 진입 금액 '사용자 정의' 규칙 (C언어 형식 if/else)

모의(trading_client.py 자동진입)와 실전(live_trader.py / live_trader_gui.py)이 같이 쓴다.
eval 을 쓰지 않는 자체 해석기라 규칙 안에서 파일·네트워크·파이썬 코드는 절대 실행 안 됨.

쓸 수 있는 변수 (읽기)
  start_seed      처음 시드 (설정 창에 넣은 값)
  current_seed    지금 총자산 = 현금 + 보유 포지션 증거금 + 평가손익  [11/22 사용자 요청: 현금 → 총자산]
                  (모의: 서버 잔고+포지션 / 실전: 바이낸스 지갑 잔고+미실현 손익 / 실전 모의: 가상 현금+포지션)
  cash            지금 현금(포지션에 안 묶인 돈. 모의: 서버 잔고 / 실전: 바이낸스 사용 가능 USDT)
  open_positions  지금 보유 중인 포지션 수 (자동진입·봇 기준)
  is_short        이번 신호가 숏이면 1, 롱이면 0
  strong_short    강한 숏(4h예상 ≤ -1.0%)이면 1, 아니면 0
  leverage        설정한 레버리지
결과 변수 (써야 함)
  position_size   이번 1회 진입 금액($). 0 이하로 두면 이번 신호는 진입 안 함
  그 밖에 새 이름(예: profit = current_seed - start_seed;)은 임시 변수로 자유롭게 써도 됨

문법
  if (조건) { ... } else if (조건) { ... } else { ... }     (중괄호 없이 한 줄도 됨)
  변수 = 식;                      세미콜론 필수
  연산: + - * / %   비교: == != < <= > >=   논리: && || !   괄호 ( )
  함수: int(x)=소수점 버림, round(x), min(a,b,...), max(a,b,...), abs(x)
  주석: // 한 줄,  /* 여러 줄 */
  '/' 는 소수 나눗셈(5/2 = 2.5). 정수로 자르려면 int() 사용

예시
  if (start_seed < current_seed) {
      position_size = int(current_seed * 0.03);
  } else {
      position_size = int(start_seed * 0.03);
  }
"""
import math
import re

INPUT_VARS = ("start_seed", "current_seed", "cash", "open_positions", "is_short", "strong_short", "leverage")
OUTPUT_VAR = "position_size"
FUNCS = {
    "int": (1, 1, lambda a: float(math.trunc(a[0]))),
    "round": (1, 1, lambda a: float(round(a[0]))),
    "abs": (1, 1, lambda a: abs(a[0])),
    "min": (1, 20, lambda a: min(a)),
    "max": (1, 20, lambda a: max(a)),
}
KEYWORDS = {"if", "else"}
MAX_LEN = 5000
MAX_STEPS = 10000

EXAMPLE = """if (start_seed < current_seed) {
    position_size = int(current_seed * 0.03);
} else {
    position_size = int(start_seed * 0.03);
}"""


class RuleError(Exception):
    def __init__(self, msg, line=None):
        self.line = line
        super().__init__(f"{line}번째 줄: {msg}" if line else msg)


_TOKEN_RE = re.compile(r"""
    (?P<ws>[ \t\r]+)
  | (?P<nl>\n)
  | (?P<lc>//[^\n]*)
  | (?P<bc>/\*.*?\*/)
  | (?P<num>\d+\.\d*|\.\d+|\d+)
  | (?P<id>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<op>==|!=|<=|>=|&&|\|\||[+\-*/%<>=!(){};,])
""", re.S | re.X)


def _tokenize(src):
    toks, pos, line = [], 0, 1
    while pos < len(src):
        m = _TOKEN_RE.match(src, pos)
        if not m:
            ch = src[pos]
            if src.startswith("/*", pos):
                raise RuleError("/* 주석이 */ 로 안 닫힘", line)
            raise RuleError(f"쓸 수 없는 글자 '{ch}'", line)
        kind, text = m.lastgroup, m.group()
        if kind == "num":
            toks.append(("num", float(text), line))
        elif kind == "id":
            toks.append(("kw" if text in KEYWORDS else "id", text, line))
        elif kind == "op":
            toks.append(("op", text, line))
        line += text.count("\n")
        pos = m.end()
    toks.append(("eof", None, line))
    return toks


class _Parser:
    def __init__(self, toks):
        self.t, self.i = toks, 0

    def peek(self, k=0):
        return self.t[min(self.i + k, len(self.t) - 1)]

    def next(self):
        tok = self.t[self.i]
        self.i += 1
        return tok

    def expect(self, val, what=None):
        tok = self.next()
        if tok[1] != val:
            got = "끝" if tok[0] == "eof" else f"'{tok[1]}'" if tok[0] != "num" else f"숫자 {tok[1]:g}"
            raise RuleError(f"'{val}' 가 와야 하는데 {got} 이(가) 있음" + (f" ({what})" if what else ""), tok[2])
        return tok

    def program(self):
        body = []
        while self.peek()[0] != "eof":
            body.append(self.stmt())
        return body

    def block(self):
        if self.peek()[1] == "{":
            self.next()
            body = []
            while self.peek()[1] != "}":
                if self.peek()[0] == "eof":
                    raise RuleError("'{' 를 닫는 '}' 가 없음", self.peek()[2])
                body.append(self.stmt())
            self.next()
            return body
        return [self.stmt()]

    def stmt(self):
        tok = self.peek()
        if tok[0] == "kw" and tok[1] == "if":
            self.next()
            self.expect("(", "if 뒤")
            cond = self.expr()
            self.expect(")", "if 조건 끝")
            then = self.block()
            els = None
            if self.peek()[0] == "kw" and self.peek()[1] == "else":
                self.next()
                els = self.block()
            return ("if", cond, then, els, tok[2])
        if tok[0] == "kw" and tok[1] == "else":
            raise RuleError("짝이 되는 if 없이 else 가 나옴", tok[2])
        if tok[0] == "id":
            if self.peek(1)[1] != "=":
                raise RuleError(f"'{tok[1]}' 다음엔 '=' 가 와야 함 (문장은 '변수 = 식;' 또는 if)", tok[2])
            name = tok[1]
            if name in INPUT_VARS:
                raise RuleError(f"'{name}' 는 읽기 전용 변수라 값을 넣을 수 없음", tok[2])
            if name in FUNCS:
                raise RuleError(f"'{name}' 는 함수 이름이라 변수로 못 씀", tok[2])
            self.next(); self.next()
            val = self.expr()
            self.expect(";", "문장 끝")
            return ("set", name, val, tok[2])
        if tok[1] == ";":
            self.next()
            return ("nop",)
        if tok[0] == "eof":
            raise RuleError("문장이 덜 끝남", tok[2])
        raise RuleError(f"여기에 '{tok[1]}' 가 올 수 없음 (문장은 '변수 = 식;' 또는 if)", tok[2])

    # 우선순위: || < && < == != < 비교 < + - < * / % < 단항
    def expr(self):
        return self.or_()

    def _bin(self, sub, ops):
        left = sub()
        while self.peek()[0] == "op" and self.peek()[1] in ops:
            op = self.next()
            left = ("bin", op[1], left, sub(), op[2])
        return left

    def or_(self):
        return self._bin(self.and_, ("||",))

    def and_(self):
        return self._bin(self.eq, ("&&",))

    def eq(self):
        return self._bin(self.rel, ("==", "!="))

    def rel(self):
        return self._bin(self.add, ("<", "<=", ">", ">="))

    def add(self):
        return self._bin(self.mul, ("+", "-"))

    def mul(self):
        return self._bin(self.unary, ("*", "/", "%"))

    def unary(self):
        tok = self.peek()
        if tok[0] == "op" and tok[1] in ("-", "+", "!"):
            self.next()
            return ("un", tok[1], self.unary(), tok[2])
        return self.atom()

    def atom(self):
        tok = self.next()
        if tok[0] == "num":
            return ("num", tok[1])
        if tok[0] == "id":
            if self.peek()[1] == "(":
                if tok[1] not in FUNCS:
                    raise RuleError(f"모르는 함수 '{tok[1]}' (쓸 수 있는 함수: {', '.join(FUNCS)})", tok[2])
                self.next()
                args = []
                if self.peek()[1] != ")":
                    args.append(self.expr())
                    while self.peek()[1] == ",":
                        self.next()
                        args.append(self.expr())
                self.expect(")", f"{tok[1]}( 닫기")
                lo, hi, _ = FUNCS[tok[1]]
                if not (lo <= len(args) <= hi):
                    raise RuleError(f"{tok[1]}() 에 값 {len(args)}개 — {lo}~{hi}개여야 함", tok[2])
                return ("call", tok[1], args, tok[2])
            if tok[1] in FUNCS:
                raise RuleError(f"'{tok[1]}' 는 함수라 '{tok[1]}(...)' 처럼 써야 함", tok[2])
            return ("var", tok[1], tok[2])
        if tok[1] == "(":
            e = self.expr()
            self.expect(")", "괄호 닫기")
            return e
        if tok[0] == "kw":
            raise RuleError(f"식 안에 '{tok[1]}' 를 쓸 수 없음", tok[2])
        if tok[0] == "eof":
            raise RuleError("식이 덜 끝남", tok[2])
        raise RuleError(f"식이 와야 하는데 '{tok[1]}' 가 있음", tok[2])


def parse(src):
    src = src or ""
    if len(src) > MAX_LEN:
        raise RuleError(f"규칙이 너무 김({len(src)}자, 최대 {MAX_LEN}자)")
    if not src.strip():
        raise RuleError("규칙이 비어 있음")
    return _Parser(_tokenize(src)).program()


class _Run:
    def __init__(self, env):
        self.env = env
        self.steps = 0

    def tick(self):
        self.steps += 1
        if self.steps > MAX_STEPS:
            raise RuleError("계산이 너무 많음")

    def stmts(self, body):
        for s in body:
            self.tick()
            if s[0] == "set":
                self.env[s[1]] = self.ev(s[2])
            elif s[0] == "if":
                if self.ev(s[1]) != 0:
                    self.stmts(s[2])
                elif s[3] is not None:
                    self.stmts(s[3])

    def ev(self, n):
        self.tick()
        k = n[0]
        if k == "num":
            return n[1]
        if k == "var":
            if n[1] not in self.env:
                raise RuleError(f"값이 정해지지 않은 변수 '{n[1]}' (철자 확인, 쓸 수 있는 변수: {', '.join(INPUT_VARS)})", n[2])
            return self.env[n[1]]
        if k == "un":
            v = self.ev(n[2])
            return -v if n[1] == "-" else v if n[1] == "+" else float(v == 0)
        if k == "call":
            args = [self.ev(a) for a in n[2]]
            return FUNCS[n[1]][2](args)
        if k == "bin":
            op = n[1]
            if op == "&&":
                return float(self.ev(n[2]) != 0 and self.ev(n[3]) != 0)
            if op == "||":
                return float(self.ev(n[2]) != 0 or self.ev(n[3]) != 0)
            a, b = self.ev(n[2]), self.ev(n[3])
            if op in ("/", "%") and b == 0:
                raise RuleError("0으로 나눔", n[4])
            r = {"+": lambda: a + b, "-": lambda: a - b, "*": lambda: a * b, "/": lambda: a / b,
                 "%": lambda: math.fmod(a, b), "<": lambda: float(a < b), "<=": lambda: float(a <= b),
                 ">": lambda: float(a > b), ">=": lambda: float(a >= b), "==": lambda: float(a == b),
                 "!=": lambda: float(a != b)}[op]()
            if isinstance(r, float) and (math.isinf(r) or math.isnan(r)):
                raise RuleError("계산 결과가 너무 큼", n[4])
            return r
        raise RuleError("알 수 없는 식")


def _reads(node, out):
    if not isinstance(node, tuple):
        return
    if node[0] == "var":
        out.append((node[1], node[2]))
    for x in node[1:]:
        if isinstance(x, tuple):
            _reads(x, out)
        elif isinstance(x, list):
            for y in x:
                _reads(y, out)


def _static_check(body):
    """모든 갈래를 보고, 어디서도 값을 안 넣는 변수를 읽으면 오류(철자 실수 잡기)."""
    assigned = set()

    def walk(b):
        for s in b:
            if s[0] == "set":
                assigned.add(s[1])
            elif s[0] == "if":
                walk(s[2])
                if s[3]:
                    walk(s[3])
    walk(body)
    reads = []
    for s in body:
        _reads(s, reads)
    known = set(INPUT_VARS) | assigned | {OUTPUT_VAR}
    for name, line in reads:
        if name not in known:
            raise RuleError(f"모르는 변수 '{name}' (철자 확인, 쓸 수 있는 변수: {', '.join(INPUT_VARS)})", line)


def _uses_output(body):
    for s in body:
        if s[0] == "set" and s[1] == OUTPUT_VAR:
            return True
        if s[0] == "if" and (_uses_output(s[2]) or (s[3] and _uses_output(s[3]))):
            return True
    return False


def run(src_or_ast, start_seed, current_seed, open_positions=0, is_short=0, strong_short=0, leverage=1, cash=None):
    """규칙을 실행해서 position_size($)를 돌려줌(음수/미지정은 0 = 진입 안 함). 문법·실행 오류는 RuleError."""
    ast = parse(src_or_ast) if isinstance(src_or_ast, str) else src_or_ast
    env = {"start_seed": float(start_seed), "current_seed": float(current_seed),
           "cash": float(current_seed if cash is None else cash),
           "open_positions": float(open_positions), "is_short": float(is_short),
           "strong_short": float(strong_short), "leverage": float(leverage), OUTPUT_VAR: 0.0}
    _Run(env).stmts(ast)
    v = env.get(OUTPUT_VAR, 0.0)
    return max(0.0, round(float(v), 2))


SAMPLES = (  # (설명, start, current, open, is_short, strong)
    ("시드 그대로", 1000, 1000, 0, 1, 0),
    ("시드 +20%", 1000, 1200, 3, 1, 0),
    ("시드 -20%", 1000, 800, 5, 1, 1),
    ("시드 -60%", 1000, 400, 2, 0, 0),
)


def check(src, start_seed=1000.0, leverage=3):
    """등록 전 문법 검사. (ok, 메시지, [(설명, current_seed, position_size)]) 반환.
    문법 + position_size 대입 여부 + 예시 값 4개로 실제 실행(0으로 나눔 등)까지 확인."""
    try:
        ast = parse(src)
        _static_check(ast)
    except RuleError as e:
        hint = ""
        if re.search(r"if\s*\([^)]*[^=!<>]=[^=]", src or ""):
            hint = " (조건에서 같은지 비교는 '==' 로 써야 함)"
        return False, f"문법 오류 — {e}{hint}", []
    if not _uses_output(ast):
        return False, f"'{OUTPUT_VAR} = ...;' 가 한 번도 없음 — 진입 금액을 정해줘야 함", []
    s0 = float(start_seed) if start_seed and float(start_seed) > 0 else 1000.0
    rows = []
    for desc, st, cur, op, sh, strong in SAMPLES:
        cur_v = s0 * cur / st
        try:
            ps = run(ast, s0, cur_v, op, sh, strong, leverage, cash=cur_v * 0.7)
        except RuleError as e:
            return False, f"실행 오류({desc}: 처음 {s0:g} / 현재 {cur_v:g}) — {e}", rows
        rows.append((desc, cur_v, ps))
        if ps > cur_v:
            return False, (f"{desc}(현재 시드 {cur_v:g})에서 position_size {ps:g} 가 현재 시드보다 큼 — "
                           f"비율(예: 0.03)을 확인해줘"), rows
    if all(r[2] <= 0 for r in rows):
        return False, "예시 4개 모두 position_size 가 0 이하 — 진입이 한 번도 안 됨", rows
    return True, "문법 OK", rows


if __name__ == "__main__":
    import sys
    src = open(sys.argv[1], encoding="utf-8").read() if len(sys.argv) > 1 else EXAMPLE
    ok, msg, rows = check(src)
    print(msg)
    for d, cur, ps in rows:
        print(f"  {d:<8} current_seed={cur:,.2f} → position_size={ps:g}")
