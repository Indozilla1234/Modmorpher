"""Java source analysis and Java -> Bedrock translation.

Everything that reads Java code goes through javalang here (see JavaSource / JavaAST /
JavaSymbolTable); the translators that turn Java statements into Bedrock script (JS)
and Molang live here as well."""
from __future__ import annotations
import math

import json
import os
import javalang
from typing import Optional, Tuple, Dict, Set, List

from javalang import tokenizer as _jtok
from javalang import parser as _jparser
from javalang import tree as jt
from mm_core import (
    OUTPUT_DIR,
    STATE,
    clean_java_artifact_name,
    log_critical_failure,
    sanitize_identifier,
)

_JMODIFIERS = {'public', 'protected', 'private', 'static', 'final', 'abstract', 'native',
               'synchronized', 'transient', 'volatile', 'strictfp', 'default'}

def _tok(cls, value, like):
    return cls(value, like.position)

def _skip_balanced(tokens, i, open_v, close_v):
    """tokens[i] is `open_v`; return index just past the matching `close_v`."""
    depth = 0
    n = len(tokens)
    while i < n:
        v = tokens[i].value
        if isinstance(tokens[i], _jtok.Separator) or isinstance(tokens[i], _jtok.Operator):
            if v == open_v:
                depth += 1
            elif v == close_v:
                depth -= 1
                if depth == 0:
                    return i + 1
        i += 1
    return n

def _consume_type(tokens, i, out):
    """Copy a (possibly generic / array / qualified) type starting at tokens[i] into `out`.

    Returns the index after the type.  Used for `instanceof Type` and record components.
    """
    n = len(tokens)
    while i < n:
        t = tokens[i]
        if isinstance(t, (_jtok.Identifier, _jtok.BasicType)):
            out.append(t)
            i += 1
        elif t.value == '.':
            out.append(t)
            i += 1
        elif t.value == '<':
            depth = 0
            while i < n:
                x = tokens[i]
                out.append(x)
                i += 1
                if x.value == '<':
                    depth += 1
                elif x.value == '>':
                    depth -= 1
                elif x.value == '>>':
                    depth -= 2
                elif x.value == '>>>':
                    depth -= 3
                if depth <= 0:
                    break
        elif t.value == '[' and i + 1 < n and tokens[i + 1].value == ']':
            out.extend([t, tokens[i + 1]])
            i += 2
        else:
            break

        if i < n and isinstance(tokens[i], _jtok.Identifier) and out[-1].value != '.':
            break
        if i < n and tokens[i].value == '[' and not (i + 1 < n and tokens[i + 1].value == ']'):
            break
    return i

def normalise_tokens(tokens):
    """Rewrite Java 16+ syntax into constructs javalang understands.

    * `x instanceof Foo f`            -> `x instanceof Foo`   (binding dropped)
    * `record R(int a, B b) impl {}`  -> `class R impl { private final int a; ... }`
    * `sealed` / `non-sealed` / `permits A, B` are removed.
    """
    out = []
    bindings: list = []
    i = 0
    n = len(tokens)
    while i < n:
        tk = tokens[i]
        v = tk.value

        if isinstance(tk, _jtok.Keyword) and v == 'instanceof':

            scrut: list = []
            k = len(out) - 1
            while k >= 0 and (isinstance(out[k], _jtok.Identifier) or out[k].value in ('.', 'this')):
                scrut.insert(0, out[k])
                k -= 1
            simple = bool(scrut) and isinstance(scrut[-1], _jtok.Identifier) and not (
                k >= 0 and out[k].value in (')', ']'))
            out.append(tk)
            i += 1
            if i < n and tokens[i].value == 'final':
                i += 1
            i = _consume_type(tokens, i, out)
            if i < n and isinstance(tokens[i], _jtok.Identifier):
                binding = tokens[i]
                i += 1
                if simple and binding.value != scrut[-1].value:
                    bindings.append((binding.value, list(scrut), len(out)))
            continue

        if isinstance(tk, _jtok.Identifier) and v == 'sealed' and i + 1 < n and (
                tokens[i + 1].value in _JMODIFIERS or tokens[i + 1].value in ('class', 'interface')):
            i += 1
            continue
        if (isinstance(tk, _jtok.Identifier) and v == 'non' and i + 2 < n and tokens[i + 1].value == '-'
                and tokens[i + 2].value == 'sealed'):
            i += 3
            continue
        if isinstance(tk, _jtok.Identifier) and v == 'permits' and i + 1 < n and isinstance(tokens[i + 1], _jtok.Identifier):
            i += 1
            while i < n and tokens[i].value != '{':
                i += 1
            continue

        if (isinstance(tk, _jtok.Identifier) and v == 'record' and i + 2 < n
                and isinstance(tokens[i + 1], _jtok.Identifier)
                and tokens[i + 2].value in ('(', '<')
                and not (out and out[-1].value == '.')):
            name_tok = tokens[i + 1]
            j = i + 2
            type_params = []
            if tokens[j].value == '<':
                j = _consume_type(tokens, j, type_params)
            if j < n and tokens[j].value == '(':
                end = _skip_balanced(tokens, j, '(', ')')
                comps = tokens[j + 1:end - 1]
                out.append(_tok(_jtok.Keyword, 'class', tk))
                out.append(name_tok)
                out.extend(type_params)

                k = end
                while k < n and tokens[k].value != '{':
                    out.append(tokens[k])
                    k += 1
                if k < n:
                    out.append(tokens[k])
                    k += 1

                cur, depth, parts = [], 0, []
                for c in comps:
                    if c.value in ('(', '<') and isinstance(c, (_jtok.Separator, _jtok.Operator)):
                        depth += 1
                    elif c.value in (')', '>') and isinstance(c, (_jtok.Separator, _jtok.Operator)):
                        depth -= 1
                    elif c.value == '>>':
                        depth -= 2
                    if c.value == ',' and depth == 0:
                        parts.append(cur)
                        cur = []
                    else:
                        cur.append(c)
                if cur:
                    parts.append(cur)
                for part in parts:

                    clean, p = [], 0
                    while p < len(part):
                        if part[p].value == '@':
                            p += 2
                            while p < len(part) and part[p].value == '.':
                                p += 2
                            if p < len(part) and part[p].value == '(':
                                p = _skip_balanced(part, p, '(', ')')
                            continue
                        clean.append(part[p])
                        p += 1
                    if len(clean) >= 2 and isinstance(clean[-1], _jtok.Identifier):
                        out.append(_tok(_jtok.Modifier, 'private', tk))
                        out.append(_tok(_jtok.Modifier, 'final', tk))
                        out.extend(clean)
                        out.append(_tok(_jtok.Separator, ';', tk))
                i = k
                continue

        out.append(tk)
        i += 1
    return _substitute_bindings(out, bindings)

def _substitute_bindings(out: list, bindings: list) -> list:
    """Replace uses of pattern-binding variables (`x instanceof Foo f` ... `f.bar()`) by the scrutinee `x`.

    The scope is the rest of the innermost enclosing block, which is where Java flow scoping
    lets the binding be used in practice.
    """
    for name, scrut, start in bindings:
        depth, end = 0, len(out)
        for j in range(start, len(out)):
            val = out[j].value
            if val == '{':
                depth += 1
            elif val == '}':
                if depth == 0:
                    end = j
                    break
                depth -= 1
        rebuilt = out[:start]
        for j in range(start, end):
            t = out[j]
            if isinstance(t, _jtok.Identifier) and t.value == name and not (j > 0 and out[j - 1].value == '.'):
                rebuilt.extend(_tok(type(s), s.value, t) for s in scrut)
            else:
                rebuilt.append(t)
        rebuilt.extend(out[end:])
        out = rebuilt
    return out

class JavaUnit:
    """A parsed Java source file: tokens, javalang tree and convenience queries."""

    def __init__(self, source: str):
        self.src = source or ''
        self.tokens: list = []
        self.tree = None
        self.error: Optional[str] = None
        self._line_starts: Optional[list] = None
        try:
            raw = list(_jtok.tokenize(self.src))
            self.tokens = normalise_tokens(raw)
            self.tree = _jparser.Parser(self.tokens).parse()
        except Exception as exc:
            at = getattr(exc, 'at', None)
            pos = getattr(at, 'position', None)
            where = f' at line {pos[0]}' if pos else ''
            self.error = f'{type(exc).__name__}{where}: {getattr(exc, "description", "") or exc}'
            self.tree = None

    @property
    def ok(self) -> bool:
        return self.tree is not None

    def walk(self, *node_types):
        """Yield every node of the given javalang types (document order)."""
        if self.tree is None:
            return
        for _, node in self.tree:
            if isinstance(node, node_types):
                yield node

    def package_name(self) -> Optional[str]:
        return self.tree.package.name if self.tree is not None and self.tree.package else None

    def import_paths(self) -> List[str]:
        out = []
        if self.tree is not None:
            for imp in self.tree.imports or []:
                out.append(imp.path + ('.*' if imp.wildcard else ''))
        return out

    def types(self) -> list:
        return list(self.walk(jt.TypeDeclaration))

    def classes(self) -> list:
        return list(self.walk(jt.ClassDeclaration))

    def primary_type(self):
        if self.tree is not None and self.tree.types:
            return self.tree.types[0]
        return None

    def methods(self, name: Optional[str] = None) -> list:
        """Method declarations (all, or by name; a name matching a class also yields its constructors)."""
        if name is None:
            return list(self.walk(jt.MethodDeclaration))
        return [m for m in self.walk(jt.MethodDeclaration, jt.ConstructorDeclaration) if m.name == name]

    def fields(self) -> list:
        return list(self.walk(jt.FieldDeclaration))

    def annotations(self) -> list:
        return list(self.walk(jt.Annotation))

    def identifiers(self) -> Set[str]:
        return {t.value.lower() for t in self.tokens if isinstance(t, _jtok.Identifier)}

    def _offsets(self):
        if self._line_starts is None:
            starts, pos = [0], 0
            for line in self.src.split('\n'):
                pos += len(line) + 1
                starts.append(pos)
            self._line_starts = starts
        return self._line_starts

    def _offset_of(self, position) -> int:
        line, col = position
        starts = self._offsets()
        return starts[line - 1] + col - 1

    def _method_token_span(self, method) -> Optional[Tuple[int, int]]:
        """(index of '{', index of matching '}') in self.tokens for a method/constructor body."""
        pos = getattr(method, 'position', None)
        if pos is None or method.body is None:
            return None
        toks = self.tokens
        n = len(toks)
        i = 0
        while i < n and tuple(toks[i].position) < tuple(pos):
            i += 1
        while i + 1 < n:
            if isinstance(toks[i], _jtok.Identifier) and toks[i].value == method.name and toks[i + 1].value == '(':
                break
            i += 1
        else:
            return None
        j = _skip_balanced(toks, i + 1, '(', ')')
        while j < n and toks[j].value != '{':
            if toks[j].value == ';':
                return None
            j += 1
        if j >= n:
            return None
        end = _skip_balanced(toks, j, '{', '}')
        return j, end - 1

    def body_text(self, method) -> Optional[str]:
        """Original source text between the braces of a method body."""
        span = self._method_token_span(method)
        if span is None:
            return None
        o, c = span
        return self.src[self._offset_of(self.tokens[o].position) + 1:self._offset_of(self.tokens[c].position)]

    def method_body_text(self, names) -> Optional[str]:
        """Body text of the first method whose name is in `names` (str or list)."""
        if isinstance(names, str):
            names = [names]
        for name in names:
            for m in self.methods(name):
                text = self.body_text(m)
                if text is not None:
                    return text
        return None

def replace_token_sequences(text: str, pattern: List[str], replacement: str) -> str:
    """Replace every occurrence of the token sequence `pattern` (token values) in Java `text`.

    Token based (javalang tokenizer), so whitespace/comments between tokens never matter.
    """
    try:
        toks = list(_jtok.tokenize(text))
    except Exception:
        return text
    starts, pos = [0], 0
    for line in text.split('\n'):
        pos += len(line) + 1
        starts.append(pos)

    def off(t):
        return starts[t.position[0] - 1] + t.position[1] - 1
    out, last, i, n, k = [], 0, 0, len(toks), len(pattern)
    while i + k <= n:
        if all(toks[i + j].value == pattern[j] for j in range(k)):
            s, e = off(toks[i]), off(toks[i + k - 1]) + len(toks[i + k - 1].value)
            out.append(text[last:s])
            out.append(replacement)
            last = e
            i += k
        else:
            i += 1
    out.append(text[last:])
    return ''.join(out)

def rename_identifiers(text: str, rename_map: Dict[str, str]) -> str:
    """Rename identifier tokens (token based; strings and comments are never touched)."""
    try:
        toks = list(_jtok.tokenize(text))
    except Exception:
        return text
    starts, pos = [0], 0
    for line in text.split('\n'):
        pos += len(line) + 1
        starts.append(pos)
    out, last = [], 0
    for t in toks:
        if isinstance(t, _jtok.Identifier) and t.value in rename_map:
            s = starts[t.position[0] - 1] + t.position[1] - 1
            out.append(text[last:s])
            out.append(rename_map[t.value])
            last = s + len(t.value)
    out.append(text[last:])
    return ''.join(out)

def has_token_sequence(text: str, pattern: List[str]) -> bool:
    try:
        vals = [t.value for t in _jtok.tokenize(text)]
    except Exception:
        return False
    k = len(pattern)
    return any(vals[i:i + k] == pattern for i in range(len(vals) - k + 1))

class Fragment:
    """A bag of AST nodes (a registration expression, an anonymous class body, a parsed class...)
    exposing the same query API as JavaUnit, so extractors work on either."""

    def __init__(self, nodes):
        self.nodes = [n for n in (nodes if isinstance(nodes, (list, tuple)) else [nodes]) if n is not None]
        self.src = ''
        self.tokens: list = []
        self.error: Optional[str] = None
        self.tree = self.nodes[0] if self.nodes else None

    @property
    def ok(self) -> bool:
        return True

    def walk(self, *node_types):
        seen = set()
        for n in self.nodes:
            if isinstance(n, jt.Node):
                for _, node in n:
                    if isinstance(node, node_types) and id(node) not in seen:
                        seen.add(id(node))
                        yield node

    def package_name(self):
        return None

    def import_paths(self) -> List[str]:
        return []

    def types(self) -> list:
        return list(self.walk(jt.TypeDeclaration))

    def classes(self) -> list:
        return list(self.walk(jt.ClassDeclaration))

    def primary_type(self):
        return None

    def methods(self, name: Optional[str] = None) -> list:
        """Method declarations (all, or by name; a name matching a class also yields its constructors)."""
        if name is None:
            return list(self.walk(jt.MethodDeclaration))
        return [m for m in self.walk(jt.MethodDeclaration, jt.ConstructorDeclaration) if m.name == name]

    def fields(self) -> list:
        return list(self.walk(jt.FieldDeclaration))

    def annotations(self) -> list:
        return list(self.walk(jt.Annotation))

    def body_text(self, method) -> Optional[str]:
        return None

    def method_body_text(self, names) -> Optional[str]:
        return None

    def identifiers(self) -> Set[str]:
        out: Set[str] = set()
        for n in self.nodes:
            if not isinstance(n, jt.Node):
                continue
            for _, node in n.filter(jt.Node):
                for attr in node.attrs:
                    val = getattr(node, attr, None)
                    if isinstance(val, str) and val and not val[0] in '"\'' and not val[:1].isdigit():
                        out.update(p.lower() for p in val.split('.') if p)
        return out

def as_unit(code):
    """str -> cached JavaUnit; JavaUnit/Fragment pass through."""
    if isinstance(code, (JavaUnit, Fragment)):
        return code
    return get_unit(code)

def method_statements(code, names) -> Optional[list]:
    """Statements of the first method named in `names` (str or list) of a source/unit/fragment."""
    if isinstance(names, str):
        names = [names]
    unit = as_unit(code)
    for name in names:
        for m in unit.methods(name):
            if m.body:
                return list(m.body)
    return None

_UNIT_CACHE: Dict[str, JavaUnit] = {}
_UNIT_CACHE_MAX = 4096

def get_unit(source: Optional[str]) -> JavaUnit:
    """Parse (once) and return the JavaUnit for `source`."""
    source = source or ''
    unit = _UNIT_CACHE.get(source)
    if unit is None:
        if len(_UNIT_CACHE) >= _UNIT_CACHE_MAX:
            _UNIT_CACHE.clear()
        unit = _UNIT_CACHE[source] = JavaUnit(source)
    return unit

def parse_statements(body_source: str) -> Optional[list]:
    """Parse a bare sequence of statements (a method body without braces)."""
    unit = get_unit('class _MM { void _m() {\n' + (body_source or '') + '\n} }')
    for m in unit.methods('_m'):
        return list(m.body or [])
    return None

def parse_expression(expr_source: str):
    """Parse a single Java expression."""
    unit = get_unit('class _MM { Object _v = ' + (expr_source or 'null').rstrip().rstrip(';') + '; }')
    for f in unit.fields():
        return f.declarators[0].initializer
    return None

def type_name(t) -> str:
    """Dotted type name without generics/array dims: `Map.Entry<K,V>[]` -> `Map.Entry`."""
    if t is None:
        return ''
    parts = []
    while t is not None:
        parts.append(getattr(t, 'name', '') or '')
        t = getattr(t, 'sub_type', None)
    return '.'.join(p for p in parts if p)

def simple_type_name(t) -> str:
    """Last segment of type_name(): `net.minecraft.Foo` -> `Foo`."""
    return type_name(t).split('.')[-1]

def strip_generics_name(name: Optional[str]) -> str:
    """`Foo<Bar<Baz>>` -> `Foo`.  Plain string helper (no regex)."""
    name = name or ''
    cut = name.find('<')
    return (name if cut < 0 else name[:cut]).strip()

_JAVA_ESCAPES = {'n': '\n', 't': '\t', 'r': '\r', 'b': '\b', 'f': '\f', '0': '\0',
                 '"': '"', "'": "'", '\\': '\\', 's': ' '}

def unescape_java_string(body: str) -> str:
    out, i, n = [], 0, len(body)
    while i < n:
        ch = body[i]
        if ch == '\\' and i + 1 < n:
            nxt = body[i + 1]
            if nxt == 'u':
                j = i + 1
                while j < n and body[j] == 'u':
                    j += 1
                try:
                    out.append(chr(int(body[j:j + 4], 16)))
                    i = j + 4
                    continue
                except ValueError:
                    pass
            if nxt in _JAVA_ESCAPES:
                out.append(_JAVA_ESCAPES[nxt])
                i += 2
                continue
        out.append(ch)
        i += 1
    return ''.join(out)

def string_value(node) -> Optional[str]:
    """Value of a Java string literal node (or None if `node` is not one)."""
    if isinstance(node, jt.Literal) and isinstance(node.value, str) and len(node.value) >= 2 \
            and node.value[0] == '"' and node.value[-1] == '"':
        return unescape_java_string(node.value[1:-1])
    return None

def number_value(node) -> Optional[float]:
    """Numeric value of a literal (with optional unary minus), else None."""
    if not isinstance(node, jt.Literal):
        return None
    sign = -1.0 if '-' in (node.prefix_operators or []) else 1.0
    text = str(node.value)
    text = text.replace('_', '')
    try:
        low = text.lower()
        if low.startswith('0x'):
            return sign * float(int(low.rstrip('l'), 16))
        if low and low[-1] in 'fdl' and not low.startswith('0x'):
            low = low[:-1]
        return sign * float(low)
    except ValueError:
        return None

def literal_text(node) -> Optional[str]:
    """Source-like text for a literal node, numeric suffixes stripped (`1.5F` -> `1.5`)."""
    if not isinstance(node, jt.Literal):
        return None
    v = str(node.value)
    pre = ''.join(node.prefix_operators or [])
    if v[:1] in '"\'' or v in ('true', 'false', 'null'):
        return pre + v
    num = number_value(node)
    if num is None:
        return pre + v
    txt = v.replace('_', '')
    if txt.lower().startswith('0x'):
        return pre + txt
    if txt and txt[-1] in 'fFdDlL':
        txt = txt[:-1]
    return pre + txt

def dotted(node) -> Optional[str]:
    """`a.b.c` for a MemberReference chain without selectors/calls, else None."""
    if isinstance(node, jt.MemberReference) and not node.selectors:
        return (node.qualifier + '.' if node.qualifier else '') + node.member
    if isinstance(node, jt.This) and not node.selectors:
        return 'this'
    return None

def call_chain(node) -> list:
    """Flatten `a.b(x).c(y)` into [('b', [x]), ('c', [y])].  `node.qualifier` stays on the node."""
    out = []
    if isinstance(node, (jt.MethodInvocation, jt.SuperMethodInvocation)):
        out.append((node.member, list(node.arguments or [])))
    for sel in getattr(node, 'selectors', None) or []:
        if isinstance(sel, jt.MethodInvocation):
            out.append((sel.member, list(sel.arguments or [])))
        elif isinstance(sel, jt.MemberReference):
            out.append((sel.member, None))
    return out

def iter_calls(node):
    """Every MethodInvocation reachable from `node` (including chained selectors)."""
    if node is None:
        return
    if isinstance(node, list):
        for item in node:
            yield from iter_calls(item)
        return
    if isinstance(node, jt.Node):
        for _, n in node.filter(jt.MethodInvocation):
            yield n

def iter_nodes(node, *node_types):
    """Every descendant of `node` (or of each node in a list) matching node_types."""
    if node is None:
        return
    if isinstance(node, list):
        for item in node:
            yield from iter_nodes(item, *node_types)
        return
    if isinstance(node, jt.Node):
        for _, n in node:
            if isinstance(n, node_types):
                yield n

def call_target(inv) -> str:
    """Qualifier text of a call (`a.b` in `a.b.c()`), '' when none."""
    q = getattr(inv, 'qualifier', None)
    return q or ''

def annotation_names(node) -> List[str]:
    return [a.name for a in (getattr(node, 'annotations', None) or [])]

def annotation_args(annotation) -> Dict[str, object]:
    """Annotation element values as {name: node}; a single value is keyed 'value'."""
    el = annotation.element
    if el is None:
        return {}
    if isinstance(el, list):
        return {p.name: p.value for p in el if isinstance(p, jt.ElementValuePair)}
    return {'value': el}

def type_args(t) -> List[str]:
    """Simple names of the generic arguments of a type: `Foo<Bar, Baz<Q>>` -> ['Bar', 'Baz']."""
    out = []
    for a in (getattr(t, 'arguments', None) or []):
        inner = getattr(a, 'type', None)
        if inner is not None:
            out.append(simple_type_name(inner))
    return out

def ref_name(node) -> Optional[str]:
    """Dotted name of `A.B.C` or `A.B.C.get()` (registry-object accessor), else None."""
    d = dotted(node)
    if d:
        return d
    if isinstance(node, jt.MethodInvocation) and node.member == 'get' and not node.arguments \
            and node.qualifier and not node.selectors:
        return node.qualifier
    return None

def first_created_type(node) -> Optional[str]:
    """Simple name of the first `new T(...)` inside `node` (or list of nodes)."""
    for c in iter_nodes(node, jt.ClassCreator):
        return simple_type_name(c.type)
    return None

def method_ref_target(node) -> Optional[str]:
    """`Foo::new` -> 'Foo'; `m -> new Foo(m)` -> 'Foo'; else None."""
    if isinstance(node, jt.MethodReference) and isinstance(node.expression, jt.MemberReference):
        return node.expression.member
    if isinstance(node, jt.LambdaExpression):
        body = node.body
        if isinstance(body, jt.ClassCreator):
            return simple_type_name(body.type)
    return None

def suffix_ci(text: str, suffix: str) -> str:
    """Remove `suffix` from the end of `text`, case-insensitively."""
    return text[:-len(suffix)] if suffix and text.lower().endswith(suffix.lower()) else text

def split_words(text: str) -> List[str]:
    """Split text into runs of [A-Za-z0-9_] characters (lexical word split, case preserved)."""
    out, cur = [], []
    for ch in text or '':
        if (ch.isascii() and ch.isalnum()) or ch == '_':
            cur.append(ch)
        elif cur:
            out.append(''.join(cur))
            cur = []
    if cur:
        out.append(''.join(cur))
    return out

def text_words(text: str) -> Set[str]:
    """Lower-cased word set of arbitrary text (paths, comments, code)."""
    return {w.lower() for w in split_words(text)}

def split_alnum(text: str) -> List[str]:
    """Split on any non-alphanumeric character (underscore included)."""
    out, cur = [], []
    for ch in text or '':
        if ch.isascii() and ch.isalnum():
            cur.append(ch)
        elif cur:
            out.append(''.join(cur))
            cur = []
    if cur:
        out.append(''.join(cur))
    return out

def split_on(text: str, chars: str) -> List[str]:
    """Split `text` on any character in `chars` (keeps empty pieces, like re.split on a class)."""
    out, cur = [], []
    for ch in text or '':
        if ch in chars:
            out.append(''.join(cur))
            cur = []
        else:
            cur.append(ch)
    out.append(''.join(cur))
    return out

def identifier_set(java_code) -> Set[str]:
    """Lower-cased identifiers of a Java source / unit / fragment."""
    return as_unit(java_code).identifiers()

def holder_members(source: str, holder: str) -> List[str]:
    """Every `holder.IDENT` pair in `source` (token based), e.g. holder='SoundEvents' -> ['AMBIENT', ...]."""
    try:
        toks = list(_jtok.tokenize(source or ''))
    except Exception:
        return []
    out = []
    for i in range(len(toks) - 2):
        if isinstance(toks[i], _jtok.Identifier) and toks[i].value == holder and toks[i + 1].value == '.' \
                and isinstance(toks[i + 2], _jtok.Identifier):
            out.append(toks[i + 2].value)
    return out

def iter_call_sites(nodes):
    """Yield (receiver_path, member, args) for every call in `nodes`, chains included.

    `this.entityData.set(a, b)`  -> ('this.entityData', 'set', [a, b])
    `x.getTag().putInt("k", 1)`  -> ('x.getTag()', 'putInt', ['k', 1])
    """
    if nodes is None:
        return
    if isinstance(nodes, list):
        for n in nodes:
            yield from iter_call_sites(n)
        return
    if not isinstance(nodes, jt.Node):
        return
    seen = set()
    for _, node in nodes.filter(jt.Primary):
        sels = getattr(node, 'selectors', None) or []
        path: List[str] = []
        if getattr(node, 'qualifier', None):
            path.append(node.qualifier)
        if isinstance(node, jt.This):
            path.append('this')
        if isinstance(node, jt.MethodInvocation):
            if id(node) not in seen:
                seen.add(id(node))
                yield ('.'.join(path), node.member, list(node.arguments or []))
            path.append(node.member + '()')
        elif isinstance(node, jt.MemberReference):
            path.append(node.member)
        for sel in sels:
            if isinstance(sel, jt.MethodInvocation):
                if id(sel) not in seen:
                    seen.add(id(sel))
                    yield ('.'.join(path), sel.member, list(sel.arguments or []))
                path.append(sel.member + '()')
            elif isinstance(sel, jt.MemberReference):
                path.append(sel.member)

def raw_number_text(node) -> Optional[str]:
    """Source text of a numeric literal including sign, suffix kept (`-1.5f`), else None."""
    if isinstance(node, jt.Literal) and str(node.value)[:1].isdigit() or (
            isinstance(node, jt.Literal) and str(node.value)[:1] == '.'):
        return ''.join(node.prefix_operators or []) + str(node.value)
    return None

def iter_call_chains(nodes):
    """Yield (receiver_path, [(member, args), ...]) for every run of chained calls.

    `this.body.setTextureOffset(0, 0).addBox(...)` -> ('this.body', [('setTextureOffset', [..]), ('addBox', [..])])
    """
    if nodes is None:
        return
    if isinstance(nodes, list):
        for n in nodes:
            yield from iter_call_chains(n)
        return
    if not isinstance(nodes, jt.Node):
        return
    selector_ids = set()
    for _, node in nodes.filter(jt.Primary):
        if id(node) in selector_ids:
            continue
        for sel in getattr(node, 'selectors', None) or []:
            selector_ids.add(id(sel))
        path: List[str] = []
        if getattr(node, 'qualifier', None):
            path.append(node.qualifier)
        if isinstance(node, jt.This):
            path.append('this')
        run: List[Tuple[str, list]] = []
        run_recv = ''
        if isinstance(node, jt.MethodInvocation):
            run_recv = '.'.join(path)
            run.append((node.member, list(node.arguments or [])))
        elif isinstance(node, jt.MemberReference):
            path.append(node.member)
        for sel in getattr(node, 'selectors', None) or []:
            if isinstance(sel, jt.MethodInvocation):
                if not run:
                    run_recv = '.'.join(path)
                run.append((sel.member, list(sel.arguments or [])))
            elif isinstance(sel, jt.MemberReference):
                if run:
                    yield run_recv, run
                    run = []
                path.append(sel.member)
        if run:
            yield run_recv, run

def node_var_name(node) -> Optional[str]:
    """`x` or `this.x` -> 'x'."""
    if isinstance(node, jt.MemberReference) and not node.selectors:
        return node.member if not node.qualifier else None
    if isinstance(node, jt.This) and node.selectors and isinstance(node.selectors[0], jt.MemberReference) \
            and len(node.selectors) == 1:
        return node.selectors[0].member
    return None

def recv_var(receiver: str) -> str:
    """`this.body` / `body` -> 'body'."""
    return receiver[5:] if receiver.startswith('this.') else receiver

def eval_const(node) -> Optional[float]:
    """Evaluate a constant numeric Java expression (literals, + - * /, casts, Math.PI, toRadians)."""
    if node is None:
        return None
    sign = -1.0 if '-' in (getattr(node, 'prefix_operators', None) or []) else 1.0
    if isinstance(node, jt.Literal):
        v = number_value(jt.Literal(value=node.value, prefix_operators=[]))
        return None if v is None else sign * v
    if isinstance(node, jt.MemberReference) and not node.selectors:
        if node.member == 'PI' and (node.qualifier or '').split('.')[-1] in ('Math', 'Mth', 'StrictMath', ''):
            return sign * math.pi
        return None
    if isinstance(node, jt.Cast):
        v = eval_const(node.expression)
        return None if v is None else sign * v
    if isinstance(node, jt.BinaryOperation):
        a, b = eval_const(node.operandl), eval_const(node.operandr)
        if a is None or b is None:
            return None
        try:
            val = {'+': a + b, '-': a - b, '*': a * b, '/': a / b if b else None}.get(node.operator)
        except Exception:
            return None
        return None if val is None else sign * val
    if isinstance(node, jt.MethodInvocation) and node.qualifier == 'Math' and node.member == 'toRadians' and len(node.arguments or []) == 1:
        v = eval_const(node.arguments[0])
        return None if v is None else sign * math.radians(v)
    return None

def _name_value_pairs(unit: JavaUnit) -> list:
    """(name, value-node) for every field initialiser and simple assignment, in source order."""
    pairs = []
    for f in unit.fields():
        for d in f.declarators:
            if d.initializer is not None:
                pairs.append((d.name, d.initializer))
    for a in unit.walk(jt.Assignment):
        lhs = a.expressionl
        if isinstance(lhs, jt.MemberReference):
            pairs.append((lhs.member, a.value))
        elif isinstance(lhs, jt.This) and lhs.selectors and isinstance(lhs.selectors[0], jt.MemberReference):
            pairs.append((lhs.selectors[0].member, a.value))
    return pairs

def _args_text(args, hook) -> str:
    return ', '.join(expr_text(a, hook) for a in (args or []))

def expr_text(node, hook=None) -> str:
    """Render a javalang expression node as source text (JS-compatible syntax).

    `hook(node)` may return a replacement string for any node (None = use default).
    Casts are dropped, numeric suffixes stripped, `new T(a)` kept as `new T(a)`.
    """
    if node is None:
        return ''
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return ', '.join(expr_text(n, hook) for n in node)
    if hook is not None:
        replaced = hook(node)
        if replaced is not None:
            return replaced
    pre = ''.join(getattr(node, 'prefix_operators', None) or [])
    post = ''.join(getattr(node, 'postfix_operators', None) or [])

    def with_sel(base: str) -> str:
        for sel in getattr(node, 'selectors', None) or []:
            ov = hook(('sel', sel)) if hook is not None else None
            if ov is not None:
                base += ov
            elif isinstance(sel, jt.ArraySelector):
                base += '[' + expr_text(sel.index, hook) + ']'
            elif isinstance(sel, jt.MethodInvocation):
                base += '.' + sel.member + '(' + _args_text(sel.arguments, hook) + ')'
                for s2 in sel.selectors or []:
                    base += _selector_text(s2, hook)
            elif isinstance(sel, jt.MemberReference):
                base += '.' + sel.member
                for s2 in sel.selectors or []:
                    base += _selector_text(s2, hook)
            elif isinstance(sel, jt.This):
                base += '.this'
            else:
                base += '.' + expr_text(sel, hook)
        return pre + base + post

    if isinstance(node, jt.Literal):
        return with_sel(_lit(node, hook))
    if isinstance(node, jt.MemberReference):
        q = node.qualifier + '.' if node.qualifier else ''
        return with_sel(q + node.member)
    if isinstance(node, jt.SuperMethodInvocation):
        return with_sel('super.' + node.member + '(' + _args_text(node.arguments, hook) + ')')
    if isinstance(node, jt.MethodInvocation):
        q = node.qualifier + '.' if node.qualifier else ''
        return with_sel(q + node.member + '(' + _args_text(node.arguments, hook) + ')')
    if isinstance(node, jt.This):
        q = node.qualifier + '.' if getattr(node, 'qualifier', None) else ''
        return with_sel(q + 'this')
    if isinstance(node, jt.ClassReference):
        return with_sel(type_name(node.type) + '.class')
    if isinstance(node, jt.BinaryOperation):
        return '(' + expr_text(node.operandl, hook) + ' ' + node.operator + ' ' + _operand_r(node, hook) + ')'
    if isinstance(node, jt.TernaryExpression):
        return ('(' + expr_text(node.condition, hook) + ' ? ' + expr_text(node.if_true, hook)
                + ' : ' + expr_text(node.if_false, hook) + ')')
    if isinstance(node, jt.Cast):
        return expr_text(node.expression, hook)
    if isinstance(node, jt.Assignment):
        return expr_text(node.expressionl, hook) + ' ' + node.type + ' ' + expr_text(node.value, hook)
    if isinstance(node, jt.ClassCreator):
        return with_sel('new ' + type_name(node.type) + '(' + _args_text(node.arguments, hook) + ')')
    if isinstance(node, jt.ArrayCreator):
        if node.initializer is not None:
            return with_sel(expr_text(node.initializer, hook))
        return with_sel('[]')
    if isinstance(node, jt.ArrayInitializer):
        return '[' + _args_text(node.initializers, hook) + ']'
    if isinstance(node, jt.LambdaExpression):
        params = ', '.join((getattr(p, 'member', None) or getattr(p, 'name', None) or 'p') for p in (node.parameters or []))
        body = node.body
        if isinstance(body, list):
            inner = ' '.join(stmt_text(s, hook) for s in body)
            return f'(({params}) => {{ {inner} }})'
        return f'(({params}) => {expr_text(body, hook)})'
    if isinstance(node, jt.MethodReference):
        return expr_text(node.expression, hook) + '.' + str(node.method.member if hasattr(node.method, 'member') else node.method)
    if isinstance(node, jt.VariableDeclarator):
        return node.name + (' = ' + expr_text(node.initializer, hook) if node.initializer is not None else '')
    if isinstance(node, (jt.BasicType, jt.ReferenceType)):
        return type_name(node)
    val = getattr(node, 'value', None)
    return str(val) if isinstance(val, (str, int, float)) else f'<{type(node).__name__}>'

def _operand_r(node, hook) -> str:

    if node.operator == 'instanceof':
        return type_name(node.operandr) if isinstance(node.operandr, (jt.ReferenceType, jt.BasicType)) else expr_text(node.operandr, hook)
    return expr_text(node.operandr, hook)

def _lit(node, hook) -> str:
    return literal_text(node) or str(node.value)

def _selector_text(sel, hook) -> str:
    ov = hook(('sel', sel)) if hook is not None else None
    if ov is not None:
        return ov
    if isinstance(sel, jt.ArraySelector):
        return '[' + expr_text(sel.index, hook) + ']'
    if isinstance(sel, jt.MethodInvocation):
        out = '.' + sel.member + '(' + _args_text(sel.arguments, hook) + ')'
        for s2 in sel.selectors or []:
            out += _selector_text(s2, hook)
        return out
    if isinstance(sel, jt.MemberReference):
        out = '.' + sel.member
        for s2 in sel.selectors or []:
            out += _selector_text(s2, hook)
        return out
    return '.' + expr_text(sel, hook)

def stmt_text(stmt, hook=None) -> str:
    """Render a simple statement node as one line of JS-compatible source."""
    if isinstance(stmt, jt.StatementExpression):
        return expr_text(stmt.expression, hook) + ';'
    if isinstance(stmt, jt.LocalVariableDeclaration):
        return ' '.join('let ' + expr_text(d, hook) + ';' for d in stmt.declarators)
    if isinstance(stmt, jt.ReturnStatement):
        return 'return' + (' ' + expr_text(stmt.expression, hook) if stmt.expression is not None else '') + ';'
    if isinstance(stmt, jt.ThrowStatement):
        return 'throw ' + expr_text(stmt.expression, hook) + ';'
    if isinstance(stmt, jt.BreakStatement):
        return 'break;'
    if isinstance(stmt, jt.ContinueStatement):
        return 'continue;'
    if isinstance(stmt, jt.BlockStatement):
        return '{ ' + ' '.join(stmt_text(s, hook) for s in stmt.statements or []) + ' }'
    if isinstance(stmt, jt.IfStatement):
        out = 'if (' + expr_text(stmt.condition, hook) + ') ' + stmt_text(stmt.then_statement, hook)
        if stmt.else_statement is not None:
            out += ' else ' + stmt_text(stmt.else_statement, hook)
        return out
    return ''

class _Untranslatable(Exception):
    """Raised when a Java construct has no safe Bedrock translation."""

_JS_RESERVED = {'function', 'var', 'let', 'const', 'in', 'of', 'delete', 'typeof', 'arguments', 'eval', 'await',
                'async', 'yield', 'export', 'import', 'with', 'void', 'debugger', 'undefined', 'NaN', 'Infinity'}
_LEVEL_TYPES = {'Level', 'LevelAccessor', 'ServerLevel', 'LevelReader', 'ClientLevel', 'WorldGenLevel', 'BlockGetter'}
_LIVING_TYPES = {'LivingEntity', 'Mob', 'PathfinderMob', 'Animal', 'Monster', 'Player', 'ServerPlayer', 'AgeableMob',
                 'TamableAnimal', 'Villager', 'Creature'}
_NUMERIC_TYPES = {'int', 'long', 'float', 'double', 'short', 'byte', 'Integer', 'Long', 'Float', 'Double', 'Number'}
_VANILLA_ENTITY_IDS = {
    'IronGolem': 'minecraft:iron_golem', 'Zombie': 'minecraft:zombie', 'Skeleton': 'minecraft:skeleton',
    'Creeper': 'minecraft:creeper', 'Villager': 'minecraft:villager', 'Cow': 'minecraft:cow', 'Pig': 'minecraft:pig',
    'Sheep': 'minecraft:sheep', 'Chicken': 'minecraft:chicken', 'Wolf': 'minecraft:wolf', 'Cat': 'minecraft:cat',
    'Spider': 'minecraft:spider', 'Enderman': 'minecraft:enderman', 'ItemEntity': 'minecraft:item',
    'ExperienceOrb': 'minecraft:xp_orb', 'Bat': 'minecraft:bat', 'Slime': 'minecraft:slime',
}
_MATH_PASSTHROUGH = {'abs', 'max', 'min', 'floor', 'ceil', 'round', 'sqrt', 'pow', 'sin', 'cos', 'tan', 'atan',
                     'atan2', 'asin', 'acos', 'random', 'hypot', 'log', 'exp', 'cbrt'}

def _is_srg_name(name: str) -> bool:
    """Detect an SRG token that escaped the upstream source deobfuscation pass.

    This is a fail-fast diagnostic only: the converter does not translate or
    maintain any SRG-to-Mojang aliases. mm_pipeline owns that mapping step.
    """
    return len(name) > 3 and name[:2] in ('m_', 'f_') and name.endswith('_') and name[2:-1].isdigit()

def _snake(name: str) -> str:
    out = []
    for i, ch in enumerate(name):
        if ch.isupper() and i and (name[i - 1].islower() or (i + 1 < len(name) and name[i + 1].islower())):
            out.append('_')
        out.append(ch.lower())
    return ''.join(out).replace('__', '_')

def _js_name(name: str) -> str:
    return name + '_' if name in _JS_RESERVED else name

def _java_one_line(node, limit: int = 170) -> str:
    try:
        if isinstance(node, jt.Statement) or isinstance(node, jt.LocalVariableDeclaration):
            text = stmt_text(node) or type(node).__name__
        else:
            text = expr_text(node)
    except Exception:
        text = type(node).__name__
    text = ' '.join(text.split())
    return text if len(text) <= limit else text[:limit - 3] + '...'

class JsTranslator:
    """Translates javalang expression / statement nodes into Bedrock Script API JavaScript."""

    def __init__(self, namespace: str = 'mod', symbols: Optional['JavaSymbolTable'] = None, player: str = 'entity'):
        self.ns = namespace or 'mod'
        self.symbols = symbols
        self.player = player
        self.kinds: Dict[str, str] = {'world': 'level', 'level': 'level', 'entity': 'entity', 'sourceentity': 'entity',
                                      'immediatesourceentity': 'entity', 'player': 'player', 'x': 'number',
                                      'y': 'number', 'z': 'number'}

    def declare(self, name: str, java_type) -> None:
        t = simple_type_name(java_type) if java_type is not None and not isinstance(java_type, str) else (java_type or '')
        if t in _LEVEL_TYPES:
            self.kinds[name] = 'level'
        elif t == 'Player' or t == 'ServerPlayer':
            self.kinds[name] = 'player'
        elif t in ('List', 'ArrayList', 'Collection', 'Set', 'LinkedList') or (
                java_type is not None and not isinstance(java_type, str) and getattr(java_type, 'dimensions', None)):
            self.kinds[name] = 'list'
        elif t in _NUMERIC_TYPES:
            self.kinds[name] = 'number'
        elif t in ('BlockState', 'Block'):
            self.kinds[name] = 'block'
        elif t == 'BlockPos':
            self.kinds[name] = 'pos'
        elif t == 'Vec3':
            self.kinds[name] = 'vec'
        elif t == 'String':
            self.kinds[name] = 'string'
        elif t in ('boolean', 'Boolean'):
            self.kinds[name] = 'bool'
        elif t.endswith('Entity') or t in _LIVING_TYPES or t == 'Entity':
            self.kinds[name] = 'entity'
        else:
            self.kinds[name] = 'unknown'

    def _id(self, holder_ns: str, const: str) -> str:
        return f'"{holder_ns}:{const.lower()}"'

    @staticmethod
    def _check_name(name: str) -> None:
        if _is_srg_name(name):
            raise _Untranslatable(f'SRG identifier {name} reached conversion; source deobfuscation did not resolve it')

    def expr(self, node) -> str:
        return self._e(node)[0]

    def _e(self, node) -> Tuple[str, str]:
        if node is None:
            raise _Untranslatable('missing expression')
        pre = ''.join(getattr(node, 'prefix_operators', None) or [])
        post = ''.join(getattr(node, 'postfix_operators', None) or [])
        js, kind = self._core(node)
        if pre == '!' and js in ('true', 'false'):
            return ('false' if js == 'true' else 'true'), 'bool'
        if pre:
            if pre.startswith('!'):
                kind = 'bool'
            if kind in ('block', 'list') and pre != '!':
                raise _Untranslatable('operator on object')
            js = f'{pre}{self._paren(js)}'
            if pre.strip('!-+~') == '':
                kind = 'bool' if pre.startswith('!') else 'number'
        if post:
            js = f'{js}{post}'
        return js, kind

    @staticmethod
    def _unparen(js: str) -> str:
        """Drop one redundant outer pair of parentheses: `((a) && (b))` -> `(a) && (b)`."""
        if js.startswith('(') and js.endswith(')') and _balanced_outer(js) and not js.startswith('(('):
            return js[1:-1]
        if js.startswith('((') and js.endswith('))') and _balanced_outer(js):
            return js[1:-1]
        return js

    @staticmethod
    def _paren(js: str) -> str:
        if js.startswith('(') and _balanced_outer(js):
            return js
        plain = js.replace('.', '').replace('_', '').replace('"', '').replace('$', '')
        return js if plain.isalnum() or (js.endswith(')') and ' ' not in js and js.count('(') == 1) else f'({js})'

    def _core(self, node) -> Tuple[str, str]:
        if isinstance(node, jt.Literal):
            return self._literal(node), self._literal_kind(node)
        if isinstance(node, (jt.MemberReference, jt.MethodInvocation, jt.This, jt.SuperMethodInvocation)):
            return self._chain(node)
        if isinstance(node, jt.BinaryOperation):
            return self._binary(node)
        if isinstance(node, jt.TernaryExpression):
            c, t, f = self.expr(node.condition), self._e(node.if_true), self._e(node.if_false)
            return f'({c} ? {t[0]} : {f[0]})', t[1]
        if isinstance(node, jt.Cast):
            inner, kind = self._e(node.expression)
            if isinstance(node.type, jt.BasicType) and node.type.name in ('int', 'long', 'short', 'byte', 'char'):
                return f'Math.trunc({inner})', 'number'
            return inner, kind
        if isinstance(node, jt.Assignment):
            return self._assignment(node), 'unknown'
        if isinstance(node, jt.ClassCreator):
            return self._creator(node)
        if isinstance(node, jt.ArrayCreator):
            if node.initializer is not None:
                return self._core(node.initializer)
            raise _Untranslatable('array allocation')
        if isinstance(node, jt.ArrayInitializer):
            return '[' + ', '.join(self.expr(a) for a in node.initializers or []) + ']', 'list'
        if isinstance(node, jt.LambdaExpression):
            return self._lambda(node), 'unknown'
        if isinstance(node, jt.ClassReference):
            return json.dumps(type_name(node.type)), 'string'
        raise _Untranslatable(type(node).__name__)

    @staticmethod
    def _literal(node) -> str:
        v = str(node.value)
        if v.startswith("'") and v.endswith("'"):
            return json.dumps(unescape_java_string(v[1:-1]))
        if v.startswith('"'):
            return json.dumps(unescape_java_string(v[1:-1])) if len(v) >= 2 else v
        return literal_text(jt.Literal(value=node.value, prefix_operators=[])) or v

    @staticmethod
    def _literal_kind(node) -> str:
        v = str(node.value)
        if v in ('true', 'false'):
            return 'bool'
        if v.startswith('"'):
            return 'string'
        if v == 'null':
            return 'null'
        return 'number'

    def _binary(self, node) -> Tuple[str, str]:
        op = node.operator
        if op == 'instanceof':
            return self._instanceof(node), 'bool'
        (a, ka), (b, kb) = self._e(node.operandl), self._e(node.operandr)
        if op in ('==', '!='):
            if 'null' in (ka, kb):
                return f'({a} {op} {b})', 'bool'
            if {ka, kb} == {'block', 'id'}:
                a, b = (a + '.typeId' if ka == 'block' else a), (b + '.typeId' if kb == 'block' else b)
            return f'({a} {op}= {b})', 'bool'
        if op in ('&&', '||'):
            if op == '&&':
                if a == 'false' or b == 'false':
                    return 'false', 'bool'
                if a == 'true':
                    return b, 'bool'
                if b == 'true':
                    return a, 'bool'
            else:
                if a == 'true' or b == 'true':
                    return 'true', 'bool'
                if a == 'false':
                    return b, 'bool'
                if b == 'false':
                    return a, 'bool'
            return f'({a} {op} {b})', 'bool'
        if op in ('<', '>', '<=', '>='):
            return f'({a} {op} {b})', 'bool'
        kind = 'string' if 'string' in (ka, kb) and op == '+' else 'number'
        return f'({a} {op} {b})', kind

    def _instanceof(self, node) -> str:
        val = self.expr(node.operandl)
        t = simple_type_name(node.operandr) if isinstance(node.operandr, (jt.ReferenceType, jt.BasicType)) else ''
        if t in ('Player', 'ServerPlayer', 'LocalPlayer'):
            return f'({val}?.typeId === "minecraft:player")'
        if t in _LIVING_TYPES or t == 'Entity':
            return f'({val}?.getComponent("minecraft:health") !== undefined)'
        if t in _LEVEL_TYPES:
            return 'true'
        if t in _VANILLA_ENTITY_IDS:
            return f'({val}?.typeId === "{_VANILLA_ENTITY_IDS[t]}")'
        base = t[:-6] if t.endswith('Entity') and len(t) > 6 else t
        return f'({val}?.typeId === "{self.ns}:{_snake(base)}")'

    def _assignment(self, node) -> str:
        mod = self._mod_variable(node.expressionl)
        if mod is not None:
            holder, field = mod
            value = self.expr(node.value)
            if node.type != '=':
                raise _Untranslatable('compound assignment to mod variable')
            return f'{holder}.setDynamicProperty("{self.ns}:{field}", {value})'
        lhs = node.expressionl
        if isinstance(lhs, jt.MemberReference) and not lhs.qualifier and not lhs.selectors:
            self._check_name(lhs.member)
            return f'{_js_name(lhs.member)} {node.type} {self.expr(node.value)}'
        raise _Untranslatable('assignment target')

    def _creator(self, node) -> Tuple[str, str]:
        t = simple_type_name(node.type)
        args = node.arguments or []
        if node.body is not None:
            raise _Untranslatable('anonymous class')
        if t == 'Vec3' and len(args) == 3:
            x, y, z = (self.expr(a) for a in args)
            return f'{{ x: {x}, y: {y}, z: {z} }}', 'vec'
        if t == 'BlockPos' and len(args) == 3:
            x, y, z = (self.expr(a) for a in args)
            return f'{{ x: Math.floor({x}), y: Math.floor({y}), z: Math.floor({z}) }}', 'pos'
        if t == 'ItemStack' and args:
            item = self.expr(args[0])
            count = self.expr(args[1]) if len(args) > 1 else '1'
            return f'new ItemStack({item}, {count})', 'unknown'
        raise _Untranslatable(f'new {t}')

    def _lambda(self, node) -> str:
        params = [_js_name(getattr(p, 'member', None) or getattr(p, 'name', 'p')) for p in node.parameters or []]
        body = node.body
        if isinstance(body, list):
            lines = self.stmts(body, 1)
            return f'(({", ".join(params)}) => {{\n' + '\n'.join(lines) + '\n})'
        return f'(({", ".join(params)}) => {self.expr(body)})'

    def _mod_variable(self, node) -> Optional[Tuple[str, str]]:
        """`XModVariables.WorldVariables.get(world).flag` -> ('<dimension/entity js>', 'flag')."""
        if not isinstance(node, jt.MethodInvocation) or node.member != 'get':
            return None
        q = node.qualifier or ''
        holder_kind = next((h for h in ('WorldVariables', 'MapVariables', 'PlayerVariables') if q.endswith(h)), None)
        if holder_kind is None or not q.split('.')[0].endswith('ModVariables') or not node.arguments:
            return None
        sels = node.selectors or []
        if len(sels) != 1 or not isinstance(sels[0], jt.MemberReference):
            return None
        target = self.expr(node.arguments[0])
        if holder_kind == 'PlayerVariables':
            return target, sels[0].member
        return 'BEDROCK_WORLD', sels[0].member

    def _segments(self, node) -> list:
        q = getattr(node, 'qualifier', None)
        segs: list = [('f', p) for p in q.split('.')] if q and not isinstance(node, jt.This) else []
        if isinstance(node, jt.This):
            segs.append(('this', None))
        elif isinstance(node, jt.MethodInvocation):
            segs.append(('c', node.member, list(node.arguments or [])))
        elif isinstance(node, jt.SuperMethodInvocation):
            raise _Untranslatable('super call')
        else:
            segs.append(('f', node.member))
        for sel in getattr(node, 'selectors', None) or []:
            if isinstance(sel, jt.MethodInvocation):
                segs.append(('c', sel.member, list(sel.arguments or [])))
            elif isinstance(sel, jt.MemberReference):
                segs.append(('f', sel.member))
            elif isinstance(sel, jt.ArraySelector):
                segs.append(('i', sel.index))
            else:
                raise _Untranslatable('selector')
        return segs

    def _chain(self, node) -> Tuple[str, str]:
        mod = self._mod_variable_read(node)
        if mod is not None:
            return mod
        segs = self._segments(node)
        for s in segs:
            if s[0] in ('f', 'c'):
                self._check_name(s[1])
        head = segs[0]
        if head[0] == 'this':
            raise _Untranslatable('this')
        if head[0] == 'f' and head[1][:1].isupper() and head[1] not in self.kinds:
            return self._static_chain(segs)
        if head[0] == 'f':
            name = head[1]
            cur, kind = _js_name(name), self.kinds.get(name, 'unknown')
            if name not in self.kinds and not (name[:1].islower() or name[:1] == '_'):
                raise _Untranslatable(f'unknown name {name}')
            rest = segs[1:]
        else:
            raise _Untranslatable(f'call to {head[1]}()')
        return self._walk(cur, kind, rest)

    def _mod_variable_read(self, node) -> Optional[Tuple[str, str]]:
        m = self._mod_variable(node)
        if m is None:
            return None
        target, field = m
        return f'({target}.getDynamicProperty("{self.ns}:{field}") ?? 0)', 'unknown'

    def _walk(self, cur: str, kind: str, segs: list) -> Tuple[str, str]:
        for seg in segs:
            if seg[0] == 'f':
                cur, kind = self._field(cur, kind, seg[1])
            elif seg[0] == 'c':
                cur, kind = self._call(cur, kind, seg[1], seg[2])
            else:
                cur, kind = f'{cur}[{self.expr(seg[1])}]', 'unknown'
        return cur, kind

    def _static_chain(self, segs: list) -> Tuple[str, str]:
        cls = segs[0][1]
        rest = segs[1:]
        if cls == 'Math':
            if rest and rest[0][0] == 'f' and rest[0][1] in ('PI', 'E'):
                return self._walk(f'Math.{rest[0][1]}', 'number', rest[1:])
            if rest and rest[0][0] == 'c':
                name, args = rest[0][1], rest[0][2]
                js = [self.expr(a) for a in args]
                if name in _MATH_PASSTHROUGH:
                    return self._walk(f'Math.{name}({", ".join(js)})', 'number', rest[1:])
                if name == 'toRadians':
                    return self._walk(f'(({js[0]}) * Math.PI / 180)', 'number', rest[1:])
                if name == 'toDegrees':
                    return self._walk(f'(({js[0]}) * 180 / Math.PI)', 'number', rest[1:])
                if name == 'signum':
                    return self._walk(f'Math.sign({js[0]})', 'number', rest[1:])
            raise _Untranslatable('Math member')
        if cls in ('Mth',):
            if rest and rest[0][0] == 'c':
                name, args = rest[0][1], rest[0][2]
                if name == 'nextInt' and len(args) == 3:
                    a, b = self.expr(args[1]), self.expr(args[2])
                    return self._walk(f'(Math.floor(Math.random() * (({b}) - ({a}) + 1)) + ({a}))', 'number', rest[1:])
                if name in ('floor', 'ceil', 'abs', 'sqrt', 'sin', 'cos') and len(args) == 1:
                    fn = {'floor': 'floor', 'ceil': 'ceil', 'abs': 'abs', 'sqrt': 'sqrt', 'sin': 'sin', 'cos': 'cos'}[name]
                    return self._walk(f'Math.{fn}({self.expr(args[0])})', 'number', rest[1:])
                if name == 'clamp' and len(args) == 3:
                    v, lo, hi = (self.expr(a) for a in args)
                    return self._walk(f'Math.min(Math.max({v}, {lo}), {hi})', 'number', rest[1:])
            raise _Untranslatable('Mth member')
        if cls in ('Blocks', 'Items', 'MobEffects', 'SoundEvents', 'ParticleTypes', 'Enchantments') and rest \
                and rest[0][0] == 'f':
            const = rest[0][1]
            if cls == 'SoundEvents':
                if const not in _SOUND_NAME_MAP:
                    raise _Untranslatable(f'no known Bedrock sound for {const}')
                ident = json.dumps(_SOUND_NAME_MAP[const])
            elif cls == 'MobEffects':
                eff = _EFFECT_NAME_MAP.get(const)
                if eff is None:
                    raise _Untranslatable(f'no Bedrock effect for {const}')
                ident = json.dumps('minecraft:' + eff)
            elif cls == 'ParticleTypes':
                if const.lower() not in JAVA_PARTICLE_MAP:
                    raise _Untranslatable(f'no Bedrock particle for {const}')
                ident = json.dumps(JAVA_PARTICLE_MAP[const.lower()])
            else:
                kind = {'Blocks': 'blocks', 'Items': 'items'}.get(cls)
                valid = bedrock_ids(kind) if kind else None
                if valid is not None and const.lower() not in valid:
                    raise _Untranslatable(f'{cls}.{const} has no Bedrock id "{const.lower()}"')
                ident = self._id('minecraft', const)
            if all(s[0] == 'c' and s[1] in ('defaultBlockState', 'getBlock', 'get', 'value') for s in rest[1:]):
                return ident, 'id'
            raise _Untranslatable(f'{cls}.{rest[0][1]} member access')
        if cls.endswith(('ModBlocks', 'ModItems', 'ModEntities', 'ModSounds', 'ModMobEffects')) and rest and rest[0][0] == 'f':
            tail = [s for s in rest[1:] if not (s[0] == 'c' and s[1] in ('get', 'defaultBlockState', 'getBlock'))]
            if not tail:
                return self._id(self.ns, rest[0][1]), 'id'
            raise _Untranslatable(f'{cls} member access')
        if cls == 'Component' and rest and rest[0][0] == 'c' and rest[0][1] in ('literal', 'translatable') and rest[0][2]:
            arg = self.expr(rest[0][2][0])
            return (arg, 'string') if rest[0][1] == 'literal' else (f'{{ translate: {arg} }}', 'unknown')
        if cls == 'BlockPos' and rest and rest[0][0] == 'c' and rest[0][1] in ('containing', 'of') and len(rest[0][2]) == 3:
            x, y, z = (self.expr(a) for a in rest[0][2])
            return self._walk(f'{{ x: Math.floor({x}), y: Math.floor({y}), z: Math.floor({z}) }}', 'pos', rest[1:])
        if cls == 'String' and rest and rest[0][0] == 'c' and rest[0][1] == 'valueOf' and rest[0][2]:
            return f'String({self.expr(rest[0][2][0])})', 'string'
        if cls in ('Integer', 'Double', 'Float', 'Long') and rest and rest[0][0] == 'c' and rest[0][1].startswith('parse') and rest[0][2]:
            return f'Number({self.expr(rest[0][2][0])})', 'number'
        raise _Untranslatable(f'static {cls}')

    def _field(self, cur: str, kind: str, name: str) -> Tuple[str, str]:
        if kind in ('entity', 'player'):
            if name == 'level':
                return f'{cur}.dimension', 'level'
            if name in ('xRot', 'yRot'):
                return f'{cur}.getRotation().{name[0]}', 'number'
            raise _Untranslatable(f'entity field {name}')
        if kind == 'level' and name == 'isClientSide':
            return 'false', 'bool'
        if kind in ('vec', 'pos') and name in ('x', 'y', 'z'):
            return f'{cur}.{name}', 'number'
        if kind == 'list' and name == 'length':
            return f'{cur}.length', 'number'
        if kind in ('unknown', 'static'):
            return f'{cur}.{name}', 'unknown'
        raise _Untranslatable(f'{kind} field {name}')

    def _call(self, cur: str, kind: str, name: str, args: list) -> Tuple[str, str]:
        A = lambda i: self.expr(args[i])
        n = len(args)
        living = kind in ('entity', 'player')
        if kind == 'level' or name in ('getServer', 'getCommands'):
            hit = self._level_call(cur, kind, name, args)
            if hit is not None:
                return hit
        if kind in ('server', 'commands'):
            hit = self._level_call(cur, kind, name, args)
            if hit is not None:
                return hit
        if living:
            hit = self._entity_call(cur, kind, name, args)
            if hit is not None:
                return hit
        if kind == 'list':
            if name == 'isEmpty' and n == 0:
                return f'({cur}.length === 0)', 'bool'
            if name == 'size' and n == 0:
                return f'{cur}.length', 'number'
            if name == 'get' and n == 1:
                return f'{cur}[{A(0)}]', 'unknown'
            if name == 'contains' and n == 1:
                return f'{cur}.includes({A(0)})', 'bool'
        if kind == 'random':
            if name == 'nextInt' and n == 1:
                return f'Math.floor(Math.random() * ({A(0)}))', 'number'
            if name == 'nextInt' and n == 2:
                return f'(Math.floor(Math.random() * (({A(1)}) - ({A(0)}))) + ({A(0)}))', 'number'
            if name in ('nextFloat', 'nextDouble') and n == 0:
                return 'Math.random()', 'number'
            if name == 'nextBoolean' and n == 0:
                return '(Math.random() < 0.5)', 'bool'
        if kind == 'block':
            if name in ('getBlock', 'getBlockState') and n == 0:
                return cur, 'block'
            if name == 'is' and n == 1:
                return f'({cur}.typeId === {A(0)})', 'bool'
        if kind in ('vec', 'pos'):
            if name in ('getX', 'getY', 'getZ') and n == 0:
                return f'{cur}.{name[3].lower()}', 'number'
        if kind == 'string':
            if name == 'equals' and n == 1:
                return f'({cur} === {A(0)})', 'bool'
            if name in ('length',) and n == 0:
                return f'{cur}.length', 'number'
            if name in ('contains',) and n == 1:
                return f'{cur}.includes({A(0)})', 'bool'
            if name in ('toLowerCase', 'toUpperCase', 'trim') and n == 0:
                return f'{cur}.{name}()', 'string'
        if kind == 'nbt':
            return self._nbt_call(cur, name, args)
        if name == 'getPersistentData' and n == 0 and living:
            return cur, 'nbt'
        if kind == 'number' and name in ('intValue', 'doubleValue', 'floatValue', 'longValue') and n == 0:
            return cur, 'number'
        raise _Untranslatable(f'{kind}.{name}()')

    def _nbt_call(self, cur: str, name: str, args: list) -> Tuple[str, str]:
        key = self.expr(args[0]) if args else '""'
        defaults = {'getInt': '0', 'getDouble': '0', 'getFloat': '0', 'getLong': '0', 'getBoolean': 'false', 'getString': '""'}
        if name in defaults:
            return f'({cur}.getDynamicProperty({key}) ?? {defaults[name]})', 'unknown'
        if name.startswith('put') and len(args) == 2:
            return f'{cur}.setDynamicProperty({key}, {self.expr(args[1])})', 'unknown'
        if name in ('contains', 'hasUUID'):
            return f'({cur}.getDynamicProperty({key}) !== undefined)', 'bool'
        if name == 'remove':
            return f'{cur}.setDynamicProperty({key}, undefined)', 'unknown'
        raise _Untranslatable(f'nbt.{name}')

    def _entity_call(self, cur: str, kind: str, name: str, args: list) -> Optional[Tuple[str, str]]:
        A = lambda i: self.expr(args[i])
        n = len(args)
        simple = {
            'getX': f'{cur}.location.x', 'getY': f'{cur}.location.y', 'getZ': f'{cur}.location.z',
            'getEyeY': f'{cur}.getHeadLocation().y',
            'getYRot': f'{cur}.getRotation().y', 'getXRot': f'{cur}.getRotation().x',
            'isSleeping': f'{cur}.isSleeping', 'isInWater': f'{cur}.isInWater', 'onGround': f'{cur}.isOnGround',
            'isOnGround': f'{cur}.isOnGround', 'isShiftKeyDown': f'{cur}.isSneaking', 'isCrouching': f'{cur}.isSneaking',
            'isSprinting': f'{cur}.isSprinting', 'isSwimming': f'{cur}.isSwimming', 'isAlive': f'{cur}.isValid()',
            'getHealth': f'{cur}.getComponent("minecraft:health")?.currentValue',
            'getMaxHealth': f'{cur}.getComponent("minecraft:health")?.effectiveMax',
            'isOnFire': f'({cur}.getComponent("minecraft:onfire") !== undefined)',
            'isPassenger': f'({cur}.getComponent("minecraft:riding") !== undefined)',
            'isBaby': f'({cur}.getComponent("minecraft:is_baby") !== undefined)',
        }
        if n == 0 and name in simple:
            k = 'number' if name in ('getX', 'getY', 'getZ', 'getEyeY', 'getYRot', 'getXRot', 'getHealth', 'getMaxHealth') else 'bool'
            return simple[name], k
        if n == 0 and name in ('level', 'getLevel'):
            return f'{cur}.dimension', 'level'
        if n == 0 and name == 'getLookAngle':
            return f'{cur}.getViewDirection()', 'vec'
        if n == 0 and name == 'getDeltaMovement':
            return f'{cur}.getVelocity()', 'vec'
        if n == 0 and name == 'position':
            return f'{cur}.location', 'vec'
        if n == 0 and name == 'blockPosition':
            return (f'{{ x: Math.floor({cur}.location.x), y: Math.floor({cur}.location.y), '
                    f'z: Math.floor({cur}.location.z) }}'), 'pos'
        if name in ('discard', 'remove') and n <= 1:
            return f'{cur}.remove()', 'unknown'
        if name == 'kill' and n == 0:
            return f'{cur}.kill()', 'unknown'
        if name in ('setSecondsOnFire', 'setRemainingFireTicks') and n == 1:
            return f'{cur}.setOnFire({A(0)})', 'unknown'
        if name == 'hurt' and n == 2:
            return f'{cur}.applyDamage({A(1)})', 'unknown'
        if name == 'setHealth' and n == 1:
            return f'{cur}.getComponent("minecraft:health")?.setCurrentValue({A(0)})', 'unknown'
        if name == 'heal' and n == 1:
            h = f'{cur}.getComponent("minecraft:health")'
            return f'{h}?.setCurrentValue(Math.min({h}.currentValue + ({A(0)}), {h}.effectiveMax))', 'unknown'
        if name in ('teleportTo', 'setPos', 'moveTo') and n >= 3:
            return f'{cur}.teleport({{ x: {A(0)}, y: {A(1)}, z: {A(2)} }})', 'unknown'
        if name == 'setDeltaMovement' and n == 3:
            return f'({cur}.clearVelocity(), {cur}.applyImpulse({{ x: {A(0)}, y: {A(1)}, z: {A(2)} }}))', 'unknown'
        if name in ('sendSystemMessage', 'displayClientMessage') and n >= 1:

            return f'({cur}.typeId === "minecraft:player" ? {cur}.sendMessage({A(0)}) : undefined)', 'unknown'
        if name == 'hasEffect' and n == 1:
            return f'({cur}.getEffect({A(0)}) !== undefined)', 'bool'
        if name in ('removeEffect',) and n == 1:
            return f'{cur}.removeEffect({A(0)})', 'unknown'
        if name == 'addEffect' and n >= 1 and isinstance(args[0], jt.ClassCreator) \
                and simple_type_name(args[0].type) == 'MobEffectInstance' and args[0].arguments:
            ea = args[0].arguments
            eid = self.expr(ea[0])
            dur = self.expr(ea[1]) if len(ea) > 1 else '600'
            amp = self.expr(ea[2]) if len(ea) > 2 else '0'
            return f'{cur}.addEffect({eid}, {dur}, {{ amplifier: {amp} }})', 'unknown'
        if name == 'getPersistentData' and n == 0:
            return cur, 'nbt'
        if name == 'getUUID' and n == 0:
            return f'{cur}.id', 'string'
        return None

    def _level_call(self, cur: str, kind: str, name: str, args: list) -> Optional[Tuple[str, str]]:
        A = lambda i: self.expr(args[i])
        n = len(args)
        if kind == 'server' and name == 'getCommands':
            return cur, 'commands'
        if name == 'getServer' and n == 0:
            return cur, 'server'
        if kind == 'commands' and name in ('performPrefixedCommand', 'performCommand') and n >= 2:
            return f'{cur}.runCommand({A(n - 1)})', 'unknown'
        if kind != 'level':
            return None
        if name == 'isClientSide' and n == 0:
            return 'false', 'bool'
        if name == 'getBlockState' and n == 1:
            return f'{cur}.getBlock({A(0)})', 'block'
        if name in ('setBlock', 'setBlockAndUpdate') and n >= 2:
            return f'{cur}.setBlockType({A(0)}, {A(1)})', 'unknown'
        if name in ('getRandom', 'random') and n == 0:
            return cur, 'random'
        if name == 'getGameTime' and n == 0:
            return 'system.currentTick', 'number'
        if name == 'players' and n == 0:
            return f'{cur}.getPlayers()', 'list'
        if name == 'getNearestPlayer' and n >= 4:
            return (f'{cur}.getPlayers({{ location: {{ x: {A(0)}, y: {A(1)}, z: {A(2)} }}, '
                    f'maxDistance: {A(3)}, closest: 1 }})[0]'), 'entity'
        if name == 'getEntitiesOfClass' and n >= 2:
            return self._entities_of_class(cur, args)
        if name == 'playSound' and n >= 5:
            return self._play_sound(cur, args)
        if name == 'sendParticles' and n >= 4:
            return f'{cur}.spawnParticle({A(0)}, {{ x: {A(1)}, y: {A(2)}, z: {A(3)} }})', 'unknown'
        return None

    def _entities_of_class(self, cur: str, args: list) -> Tuple[str, str]:
        cls = type_name(args[0].type) if isinstance(args[0], jt.ClassReference) else None
        box = args[1]
        if not (isinstance(box, jt.MethodInvocation) and box.member == 'ofSize' and box.qualifier == 'AABB'
                and len(box.arguments or []) == 4):
            raise _Untranslatable('AABB query')
        center = self.expr(box.arguments[0])
        dims = [self.expr(a) for a in box.arguments[1:]]
        radius = f'Math.max({", ".join(dims)}) / 2'
        loc = f'location: {center}, maxDistance: {radius}'
        simple = cls.split('.')[-1] if cls else ''
        if simple in ('Player', 'ServerPlayer'):
            js = f'{cur}.getPlayers({{ {loc} }})'
        else:
            tid = _VANILLA_ENTITY_IDS.get(simple)
            if tid is None and simple not in _LIVING_TYPES and simple != 'Entity':
                base = simple[:-6] if simple.endswith('Entity') and len(simple) > 6 else simple
                tid = f'{self.ns}:{_snake(base)}'
            js = f'{cur}.getEntities({{ {loc}' + (f', type: "{tid}"' if tid else '') + ' })'
        if len(args) > 2 and isinstance(args[2], jt.LambdaExpression):
            body = args[2].body
            if not (isinstance(body, jt.Literal) and body.value == 'true'):
                js += f'.filter({self._lambda(args[2])})'
        return js, 'list'

    def _play_sound(self, cur: str, args: list) -> Tuple[str, str]:
        n = len(args)
        if n >= 8:
            loc = f'{{ x: {self.expr(args[1])}, y: {self.expr(args[2])}, z: {self.expr(args[3])} }}'
            snd, vol, pitch = self.expr(args[4]), self.expr(args[6]), self.expr(args[7])
        elif n >= 6:
            loc = self.expr(args[1])
            snd, vol, pitch = self.expr(args[2]), self.expr(args[4]), self.expr(args[5])
        else:
            raise _Untranslatable('playSound overload')
        return f'{cur}.playSound({snd}, {loc}, {{ volume: {vol}, pitch: {pitch} }})', 'unknown'

    def stmts(self, stmts, depth: int) -> List[str]:
        out: List[str] = []
        for s in stmts or []:
            out.extend(self.stmt(s, depth))
        return out

    def _todo(self, node, depth: int, reason: str) -> List[str]:
        pad = '    ' * depth
        return [f'{pad}// TODO: untranslated Java ({reason}): {_java_one_line(node)}']

    def _body(self, node, depth: int) -> List[str]:
        if node is None:
            return []
        if isinstance(node, jt.BlockStatement):
            return self.stmts(node.statements, depth)
        return self.stmt(node, depth)

    def stmt(self, s, depth: int) -> List[str]:
        pad = '    ' * depth
        try:
            return self._stmt(s, depth, pad)
        except _Untranslatable as why:
            return self._todo(s, depth, str(why))
        except Exception as exc:
            return self._todo(s, depth, f'translator error: {type(exc).__name__}')

    def _stmt(self, s, depth: int, pad: str) -> List[str]:
        if isinstance(s, jt.StatementExpression):
            return [f'{pad}{self.expr(s.expression)};']
        if isinstance(s, jt.LocalVariableDeclaration):
            out = []
            for d in s.declarators:
                self.declare(d.name, s.type)
                name = _js_name(d.name)
                if d.initializer is None:
                    out.append(f'{pad}let {name};')
                    continue
                try:
                    js, kind = self._e(d.initializer)
                    if self.kinds.get(d.name) == 'unknown' and kind not in ('unknown', 'null'):
                        self.kinds[d.name] = kind
                    out.append(f'{pad}let {name} = {js};')
                except _Untranslatable as why:
                    out.append(f'{pad}let {name}; // TODO: untranslated Java initialiser ({why}): '
                               f'{_java_one_line(d.initializer, 120)}')
            return out
        if isinstance(s, jt.IfStatement):
            return self._if(s, depth, pad)
        if isinstance(s, jt.ReturnStatement):
            return [f'{pad}return{" " + self.expr(s.expression) if s.expression is not None else ""};']
        if isinstance(s, jt.BreakStatement):
            return [f'{pad}break;']
        if isinstance(s, jt.ContinueStatement):
            return [f'{pad}continue;']
        if isinstance(s, jt.ThrowStatement):
            return [f'{pad}throw new Error("java exception");']
        if isinstance(s, jt.BlockStatement):
            return [f'{pad}{{'] + self.stmts(s.statements, depth + 1) + [f'{pad}}}']
        if isinstance(s, jt.WhileStatement):
            return [f'{pad}while ({self._unparen(self.expr(s.condition))}) {{'] + self._body(s.body, depth + 1) + [f'{pad}}}']
        if isinstance(s, jt.DoStatement):
            return [f'{pad}do {{'] + self._body(s.body, depth + 1) + [f'{pad}}} while ({self.expr(s.condition)});']
        if isinstance(s, jt.ForStatement):
            return self._for(s, depth, pad)
        if isinstance(s, jt.TryStatement):
            out = [f'{pad}try {{'] + self.stmts(s.block, depth + 1) + [f'{pad}}} catch (_error) {{', f'{pad}}}']
            if s.catches:
                out = [f'{pad}try {{'] + self.stmts(s.block, depth + 1) + [f'{pad}}} catch (_error) {{'] + \
                      self.stmts(s.catches[0].block, depth + 1) + [f'{pad}}}']
            if s.finally_block:
                out[-1] += ' finally {'
                out += self.stmts(s.finally_block, depth + 1) + [f'{pad}}}']
            return out
        if isinstance(s, jt.SwitchStatement):
            out = [f'{pad}switch ({self.expr(s.expression)}) {{']
            for case in s.cases:
                labels = case.case or []
                if not labels:
                    out.append(f'{pad}    default:')
                for lab in labels:
                    out.append(f'{pad}    case {self.expr(lab)}:')
                out.extend(self.stmts(case.statements, depth + 2))
            return out + [f'{pad}}}']
        if isinstance(s, jt.AssertStatement) or isinstance(s, jt.SynchronizedStatement):
            raise _Untranslatable(type(s).__name__)
        raise _Untranslatable(type(s).__name__)

    def _if(self, s, depth: int, pad: str, chained: bool = False) -> List[str]:
        cond = self._unparen(self.expr(s.condition))
        if cond == 'false':
            if s.else_statement is None:
                return [f'{pad}// (client-side only branch removed)']
            return self._body(s.else_statement, depth)
        if cond == 'true':
            return self._body(s.then_statement, depth)
        then_lines = self._body(s.then_statement, depth + 1)
        else_lines: List[str] = []
        is_chain = isinstance(s.else_statement, jt.IfStatement)
        if s.else_statement is not None and not is_chain:
            else_lines = self._body(s.else_statement, depth + 1)
        if not then_lines and not else_lines and not is_chain:
            return []
        out = [f'{pad}if ({cond}) {{'] + then_lines
        if is_chain:
            try:
                nested = self._if(s.else_statement, depth, pad, True)
            except _Untranslatable as why:
                nested = None
                out += [f'{pad}}} else {{'] + self._todo(s.else_statement, depth + 1, str(why)) + [f'{pad}}}']
            if nested is not None:
                if nested and nested[0].lstrip().startswith('if (') and nested[0].startswith(pad + 'if'):
                    out.append(f'{pad}}} else ' + nested[0][len(pad):])
                    out.extend(nested[1:])
                else:
                    out += [f'{pad}}} else {{'] + [('    ' + l if l.strip() else l) for l in nested] + [f'{pad}}}']
            return out
        out.append(f'{pad}}}')
        if else_lines:
            out[-1] = f'{pad}}} else {{'
            out += else_lines + [f'{pad}}}']
        return out

    def _for(self, s, depth: int, pad: str) -> List[str]:
        ctl = s.control
        if isinstance(ctl, jt.EnhancedForControl):
            var = ctl.var
            vname = var.declarators[0].name
            self.declare(vname, var.type)
            return [f'{pad}for (const {_js_name(vname)} of {self.expr(ctl.iterable)}) {{'] + \
                self._body(s.body, depth + 1) + [f'{pad}}}']
        init = ''
        if isinstance(ctl.init, jt.VariableDeclaration):
            parts = []
            for d in ctl.init.declarators:
                self.declare(d.name, ctl.init.type)
                parts.append(f'{_js_name(d.name)}' + (f' = {self.expr(d.initializer)}' if d.initializer is not None else ''))
            init = 'let ' + ', '.join(parts)
        elif ctl.init:
            init = ', '.join(self.expr(e) for e in ctl.init)
        cond = self._unparen(self.expr(ctl.condition)) if ctl.condition is not None else ''
        upd = ', '.join(self.expr(e) for e in ctl.update or [])
        return [f'{pad}for ({init}; {cond}; {upd}) {{'] + self._body(s.body, depth + 1) + [f'{pad}}}']

def translate_expression(expr: object, symbol_table=None, namespace: str = 'mod') -> Optional[str]:
    """Translate a Java expression node to JS, or None when it has no safe Bedrock equivalent."""
    if expr is None:
        return None
    if isinstance(expr, str):
        return expr
    try:
        return JsTranslator(namespace, symbol_table).expr(expr)
    except _Untranslatable:
        return None

def translate_statement(stmt: object, player: str, namespace: str, symbol_table: Optional['JavaSymbolTable'] = None) -> list:
    """Translate one Java statement; untranslatable parts become `// TODO` comments."""
    return JsTranslator(namespace, symbol_table, player).stmt(stmt, 1)

def detect_tick_method(java_code: str) -> Optional[Tuple[str, str]]:
    for node in get_unit(java_code).methods():
        if node.name in ('onTick', 'tick', 'serverTick', 'update', 'doUpdate'):
            method_body = []
            for stmt in node.body or []:
                method_body.extend(translate_statement(stmt, 'this', 'namespace'))
            return (node.name, '\n'.join(method_body))
    return None

def generate_tick_handler_js(namespace: str, entity_id: str, tick_logic: str) -> list:
    lines = [
        f"world.afterEvents.entitySpawn.subscribe((e) => {{",
        f"    if (!e.entity.typeId.includes('{namespace}:{entity_id}')) return;",
        f"    const tick_id = setInterval(() => {{",
        f"        {tick_logic}",
        f"    }}, 50); // Bedrock tick = ~50ms in scripting API",
        f"    e.entity.onTickEventCalls = (e.entity.onTickEventCalls || 0) + 1;",
        f"}});",
    ]
    return lines
def _balanced_outer(text: str) -> bool:
    """True if the first '(' of `text` closes at its very end (so the pair is redundant)."""
    depth = 0
    for i, ch in enumerate(text):
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
            if depth == 0:
                return i == len(text) - 1
    return False

class MoLangBridge:
    """Java expression -> Molang, done on the javalang AST (no text substitution)."""

    _MATH_FUNCS = {'sin': 'sin', 'cos': 'cos', 'tan': 'tan', 'asin': 'asin', 'acos': 'acos',
                   'atan': 'atan', 'atan2': 'atan', 'sqrt': 'sqrt', 'abs': 'abs', 'floor': 'floor',
                   'ceil': 'ceil', 'round': 'round', 'min': 'min', 'max': 'max', 'clamp': 'clamp',
                   'lerp': 'lerp'}

    _QUERY_CALLS = {
        'isInWater': 'query.is_in_water', 'isOnGround': 'query.is_on_ground',
        'isSprinting': 'query.is_sprinting', 'isSneaking': 'query.is_sneaking',
        'isSwimming': 'query.is_swimming', 'isBaby': 'query.is_baby', 'isOnFire': 'query.is_on_fire',
        'getHealth': 'query.health', 'getSpeed': 'query.ground_speed',
    }
    _TICK_NAMES = {'tickCount', 'animationTick'}
    _ENTITY_FIELDS = {'xRot': 'query.body_x_rotation', 'yRot': 'query.body_y_rotation'}

    @staticmethod
    def _hook(n):
        T = MoLangBridge
        if isinstance(n, jt.MethodInvocation) and not n.selectors:
            args = [expr_text(a, T._hook) for a in n.arguments or []]
            if n.qualifier == 'Math':
                if n.member == 'pow' and len(args) == 2:
                    return f'({args[0]} * {args[0]})' if args[1] in ('2', '2.0') else f'math.pow({args[0]}, {args[1]})'
                if n.member in T._MATH_FUNCS:
                    return f'math.{T._MATH_FUNCS[n.member]}({", ".join(args)})'
                if n.member == 'toRadians' and args:
                    return f'({args[0]} * 0.01745329)'
                if n.member == 'toDegrees' and args:
                    return f'({args[0]} * 57.2957795)'
                if n.member == 'random':
                    return 'math.random(0, 1)'
            if n.qualifier == 'entity' and not args and n.member in T._QUERY_CALLS:
                return T._QUERY_CALLS[n.member]
        if isinstance(n, jt.MemberReference) and not n.selectors:
            if n.qualifier == 'Math' and n.member == 'PI':
                return '3.14159265'
            if n.qualifier in (None, '', 'this', 'entity') and n.member in T._TICK_NAMES | {'partialTick'}:
                return 'query.anim_time' if n.member == 'partialTick' else 'query.anim_time * 20'
            if n.qualifier == 'entity' and n.member in T._ENTITY_FIELDS:
                return T._ENTITY_FIELDS[n.member]
        if isinstance(n, jt.This) and not n.qualifier and n.selectors:
            first = n.selectors[0]
            if isinstance(first, jt.MemberReference):
                if first.member in T._TICK_NAMES:
                    return 'query.anim_time * 20'
                return 'variable.' + first.member
        return None

    @staticmethod
    def java_to_molang(java_expr: str) -> str:
        text = (java_expr or '').strip()
        if text.startswith('return') and text[6:7].isspace():
            text = text[6:].strip()
        text = text.rstrip(';').strip()
        node = parse_expression(text)
        if node is None:
            return text
        out = expr_text(node, MoLangBridge._hook)

        if out.startswith('(') and out.endswith(')') and _balanced_outer(out):
            out = out[1:-1]
        return out.strip()

    @staticmethod
    def build_animation_json_entry(bone: str, channel: str,
                                   java_expr: str, namespace: str) -> dict:
        molang = MoLangBridge.java_to_molang(java_expr)
        entry = {
            "0.0": {
                channel: [molang, "0.0", "0.0"]
                if channel != 'rotation' else [molang, "0.0", "0.0"]
            }
        }
        return entry

    @staticmethod
    def inject_molang_into_anim_file(anim_path: str, entity_name: str,
                                     bone_channel_map: Dict[str, Dict[str, str]]) -> None:
        if not os.path.exists(anim_path):
            return
        try:
            with open(anim_path, 'r', encoding='utf-8') as fh:
                data = json.load(fh)
        except Exception:
            return
        animations = data.get('animations', {})
        for anim_id, anim_body in animations.items():
            if entity_name not in anim_id:
                continue
            bones = anim_body.setdefault('bones', {})
            for bone, channels in bone_channel_map.items():
                bone_entry = bones.setdefault(bone, {})
                for ch, java_expr in channels.items():
                    molang = MoLangBridge.java_to_molang(java_expr)
                    bone_entry[ch] = molang
        with open(anim_path, 'w', encoding='utf-8') as fh:
            json.dump(data, fh, indent=2)

class AnimationControllerGenerator:

    @staticmethod
    def generate_default_controller(entity_name: str, animations: Dict[str, str]) -> dict:
        controller_name = f"controller.animation.{entity_name}.default"
        states = {
            "default": {
                "animations": list(animations.keys())[:1],
            }
        }

        for anim_name in list(animations.keys())[1:]:
            states[anim_name] = {
                "animations": [anim_name],
                "transitions": [{"default": "!variable.playing_" + anim_name}]
            }

        return {
            "format_version": "1.10.0",
            "animation_controllers": {
                controller_name: {
                    "states": states
                }
            }
        }

class NBTTranslator:

    NBT_TO_BEDROCK_MAP = {
        'readAdditionalSaveData': 'getDynamicProperty',
        'addAdditionalSaveData':  'setDynamicProperty',
        'getInt':    'getDynamicProperty', 'putInt':    'setDynamicProperty',
        'getString': 'getDynamicProperty', 'putString': 'setDynamicProperty',
        'getFloat':  'getDynamicProperty', 'putFloat':  'setDynamicProperty',
        'getBoolean':'getDynamicProperty', 'putBoolean':'setDynamicProperty',
        'getDouble': 'getDynamicProperty', 'putDouble': 'setDynamicProperty',
        'getLong':   'getDynamicProperty', 'putLong':   'setDynamicProperty',
        'getByte':   'getDynamicProperty', 'putByte':   'setDynamicProperty',
        'getList':   'getDynamicProperty', 'put':       'setDynamicProperty',
        'getCompound': 'getDynamicProperty',
    }

    _TYPE_HINTS = {
        'getInt': 'number', 'getFloat': 'number', 'getDouble': 'number',
        'getLong': 'number', 'getByte': 'number', 'getShort': 'number',
        'getString': 'string', 'getBoolean': 'boolean',
        'getList': 'array',   'getCompound': 'object',
    }

    @staticmethod
    def translate_nbt_call(method: str, args, namespace: str,
                           entity_var: str = 'entity') -> Optional[str]:
        bedrock = NBTTranslator.NBT_TO_BEDROCK_MAP.get(method)
        if bedrock is None:
            return None
        if not args:
            return None

        key_arg = string_value(args[0])
        if key_arg is None:
            key_arg = next((v for v in (string_value(a) for a in iter_nodes(args, jt.Literal)) if v), 'unknown')
        prop_key = f'"{namespace}:{key_arg}"'
        type_hint = NBTTranslator._TYPE_HINTS.get(method, 'any')
        default = {'number': 0, 'string': '""""', 'boolean': 'false',
                   'array': '[]', 'object': '{}'}.get(type_hint, 'null')
        if method.startswith('get') or method == 'readAdditionalSaveData':
            return f'    ({entity_var}.getDynamicProperty({prop_key}) ?? {default})'
        elif method.startswith('put') or method == 'addAdditionalSaveData':
            val_arg = 'value'
            if len(args) > 1:
                lit = args[1]
                if isinstance(lit, javalang.tree.Literal):
                    val_arg = lit.value
            return f'    {entity_var}.setDynamicProperty({prop_key}, {val_arg});'
        return None

class RecursiveNBTSerializer:

    _MAX_VALUE_LEN = 32_000

    @staticmethod
    def flatten(nbt: dict, prefix: str = '', depth: int = 0,
                _out: Optional[list] = None) -> list:
        if _out is None:
            _out = []
        if depth > 16:

            _out.append((prefix, json.dumps(nbt)[:RecursiveNBTSerializer._MAX_VALUE_LEN]))
            return _out
        for k, v in nbt.items():
            path = f'{prefix}.{k}' if prefix else k
            if isinstance(v, dict):
                RecursiveNBTSerializer.flatten(v, path, depth + 1, _out)
            elif isinstance(v, list):
                for i, item in enumerate(v):
                    item_path = f'{path}.{i}'
                    if isinstance(item, dict):
                        RecursiveNBTSerializer.flatten(item, item_path, depth + 1, _out)
                    else:
                        _out.append((item_path, item))
            else:

                _out.append((path, v))
        return _out

    @staticmethod
    def emit_set_js(nbt: dict, namespace: str, entity_var: str = 'entity') -> list:
        lines = [f'// NBT serialization — {len(nbt)} top-level keys']
        pairs = RecursiveNBTSerializer.flatten(nbt)
        for dot_path, value in pairs:
            prop_key = f'{namespace}:{dot_path}'
            if isinstance(value, str):
                js_val = json.dumps(value)
            elif isinstance(value, bool):
                js_val = 'true' if value else 'false'
            elif value is None:
                js_val = 'null'
            else:
                js_val = str(value)
            if len(js_val) > RecursiveNBTSerializer._MAX_VALUE_LEN:
                js_val = json.dumps(js_val[:RecursiveNBTSerializer._MAX_VALUE_LEN])
            lines.append(f'{entity_var}.setDynamicProperty("{prop_key}", {js_val});')
        return lines

    @staticmethod
    def reconstruct_js(dot_paths: list, namespace: str,
                       entity_var: str = 'entity',
                       out_var: str = 'nbt') -> list:
        lines = [
            f'const {out_var} = {{}};',
            f'const _set = (obj, path, val) => {{',
            f'    const parts = path.split(".");',
            f'    let cur = obj;',
            f'    for (let i = 0; i < parts.length - 1; i++) {{',
            f'        const k = isNaN(parts[i]) ? parts[i] : +parts[i];',
            f'        if (cur[k] === undefined) cur[k] = isNaN(parts[i+1]) ? {{}} : [];',
            f'        cur = cur[k];',
            f'    }}',
            f'    cur[parts[parts.length-1]] = val;',
            f'}};',
        ]
        for path in dot_paths:
            prop_key = f'{namespace}:{path}'
            lines.append(
                f'_set({out_var}, "{path}", {entity_var}.getDynamicProperty("{prop_key}"));')
        lines.append(f'// {out_var} is now the reconstructed nested object')
        return lines

    @staticmethod
    def scan_and_emit_nbt_scripts(java_code: str, entity_id: str,
                                   namespace: str, bp_folder: str) -> None:
        safe = sanitize_identifier(entity_id.split(':')[-1])
        write_calls: list = []
        read_calls:  list = []

        unit = get_unit(java_code)
        _NBT_VARS = ('tag', 'nbt', 'compound')
        for m in unit.methods('addAdditionalSaveData'):
            for call in iter_calls(m.body):
                key = string_value(call.arguments[0]) if call.arguments else None
                if call.qualifier in _NBT_VARS and call.member.startswith('put') and key is not None and len(call.arguments) > 1:
                    prop = f'{namespace}:{safe}.{key}'
                    write_calls.append(f'entity.setDynamicProperty("{prop}", {expr_text(call.arguments[1])});')
        for m in unit.methods('readAdditionalSaveData'):
            for call in iter_calls(m.body):
                key = string_value(call.arguments[0]) if len(call.arguments or []) == 1 else None
                if call.qualifier in _NBT_VARS and call.member.startswith('get') and key is not None:
                    prop = f'{namespace}:{safe}.{key}'
                    default = {'getInt':'0','getFloat':'0','getDouble':'0',
                               'getString':'\'\'','getBoolean':'false','getLong':'0'}.get(call.member, 'null')
                    read_calls.append(
                        f'const {key} = entity.getDynamicProperty("{prop}") ?? {default};')

        if not write_calls and not read_calls:
            return

        lines = [
            f'import {{ world }} from "@minecraft/server";',
            '',
            f'// Auto-generated NBT serializer for {entity_id}',
            f'export function saveNBT_{safe}(entity) {{',
        ] + [f'    {l}' for l in write_calls] + [
            '}',
            '',
            f'export function loadNBT_{safe}(entity) {{',
        ] + [f'    {l}' for l in read_calls] + [
            '}',
            '',
        ]
        out_path = os.path.join(bp_folder, 'scripts', f'{safe}_nbt.js')
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, 'w', encoding='utf-8') as fh:
            fh.write('\n'.join(lines))

class CapabilityRegistry:

    CAPABILITY_TYPES = {
        'IEnergyStorage': {
            'properties': ['energy_stored', 'max_energy'],
            'methods': ['receiveEnergy', 'extractEnergy', 'getEnergyStored', 'getMaxEnergyStored'],
            'bedrock_type': 'number',
        },
        'IFluidHandler': {
            'properties': ['fluid_amount', 'fluid_type', 'fluid_capacity'],
            'methods': ['fill', 'drain', 'getFluidAmount', 'getTankCapacity'],
            'bedrock_type': 'compound',
        },
        'IItemHandler': {
            'properties': ['slot_contents', 'slot_count'],
            'methods': ['insertItem', 'extractItem', 'getStackInSlot', 'getSlots'],
            'bedrock_type': 'itemstack',
        },
    }

    @staticmethod
    def generate_capability_manager(capability_type: str, namespace: str, entity_id: str) -> list:
        lines = [
            'import { ItemStack } from "@minecraft/server";',
            "",
        ]

        if capability_type not in CapabilityRegistry.CAPABILITY_TYPES:
            return lines

        cap = CapabilityRegistry.CAPABILITY_TYPES[capability_type]

        lines.append(f"// {capability_type} Manager for {entity_id}")
        lines.append(f"const {entity_id}_capabilities = {{")

        for prop in cap['properties']:
            lines.append(f"    {prop}: 0,")

        lines.append(f"}};")
        lines.append("")

        for method in cap['methods']:
            if method.startswith('get'):
                prop_name = method[3:].lower()
                lines.append(f"function {entity_id}_{method}(entity) {{")
                lines.append(f'    return entity.getDynamicProperty("{namespace}:{entity_id}_{prop_name}") || 0;')
                lines.append(f"}}")
            elif method in ['receiveEnergy', 'fill']:
                lines.append(f"function {entity_id}_{method}(entity, amount) {{")
                lines.append(f'    const current = entity.getDynamicProperty("{namespace}:{entity_id}_stored") || 0;')
                lines.append(f'    const max = entity.getDynamicProperty("{namespace}:{entity_id}_max") || 1000;')
                lines.append(f"    const accepted = Math.min(amount, max - current);")
                lines.append(f'    entity.setDynamicProperty("{namespace}:{entity_id}_stored", current + accepted);')
                lines.append(f"    return accepted;")
                lines.append(f"}}")
            elif method in ['extractEnergy', 'drain']:
                lines.append(f"function {entity_id}_{method}(entity, amount) {{")
                lines.append(f'    const current = entity.getDynamicProperty("{namespace}:{entity_id}_stored") || 0;')
                lines.append(f"    const extracted = Math.min(amount, current);")
                lines.append(f'    entity.setDynamicProperty("{namespace}:{entity_id}_stored", current - extracted);')
                lines.append(f"    return extracted;")
                lines.append(f"}}")

        lines.append("")
        return lines

class EventRouter:

    FORGE_TO_BEDROCK: Dict[str, tuple] = {

        'LivingHurtEvent':                      ('entityHurt',                 'entity',  False),
        'LivingDamageEvent':                    ('entityHurt',                 'entity',  False),
        'LivingDeathEvent':                     ('entityDie',                  'entity',  False),
        'LivingKnockBackEvent':                 ('entityHurt',                 'entity',  True),

        'PlayerInteractEvent.RightClickEntity': ('playerInteractWithEntity',   'player',  True),
        'PlayerInteractEvent.RightClickBlock':  ('playerPlaceBlock',           'player',  True),
        'PlayerInteractEvent.LeftClickBlock':   ('playerBreakBlock',           'player',  True),
        'PlayerInteractEvent.LeftClickEmpty':   ('playerBreakBlock',           'player',  False),

        'BlockEvent.BreakEvent':                ('playerBreakBlock',           'player',  True),
        'BlockEvent.PlaceEvent':                ('playerPlaceBlock',           'player',  True),
        'BlockEvent.EntityPlaceEvent':          ('playerPlaceBlock',           'entity',  True),
        'BlockEvent.EntityMultiPlaceEvent':     ('playerPlaceBlock',           'entity',  True),

        'ItemPickupEvent':                      ('entityPickUpItem',           'entity',  False),
        'ItemTossEvent':                        ('entityDropItem',             'entity',  True),
        'AnvilRepairEvent':                     ('playerInteractWithEntity',   'player',  False),
        'PlayerDestroyItemEvent':               ('playerBreakBlock',           'player',  False),

        'EntityJoinWorldEvent':                 ('entitySpawn',                'entity',  False),
        'EntityJoinLevelEvent':                 ('entitySpawn',                'entity',  False),
        'EntityLeaveWorldEvent':                ('entityRemove',               'entity',  False),
        'EntityLeaveLevelEvent':                ('entityRemove',               'entity',  False),
        'EntityTeleportEvent':                  ('entityHitBlock',             'entity',  True),
        'EntityMountEvent':                     ('playerInteractWithEntity',   'player',  True),

        'PlayerLoggedInEvent':                  ('playerJoin',                 'player',  False),
        'PlayerLoggedOutEvent':                 ('playerLeave',                'player',  False),
        'PlayerChangedDimensionEvent':          ('playerDimensionChange',      'player',  False),
        'PlayerRespawnEvent':                   ('playerSpawn',                'player',  False),

        'TickEvent.ServerTickEvent':            ('worldInitialize',            None,      False),
        'TickEvent.ClientTickEvent':            ('tick',                       None,      False),
        'TickEvent.LevelTickEvent':             ('worldInitialize',            None,      False),
        'LevelTickEvent':                       ('worldInitialize',            None,      False),

        'ProjectileImpactEvent':                ('projectileHitBlock',         'entity',  False),
        'ArrowNockEvent':                       ('playerInteractWithEntity',   'player',  True),

        'ServerChatEvent':                      ('chatSend',                   'player',  True),
        'CommandEvent':                         ('chatSend',                   'player',  False),

        'ExplosionEvent.Detonate':              ('explosion',                  None,      False),
        'FillBucketEvent':                      ('playerInteractWithBlock',    'player',  True),
    }

    @staticmethod
    def generate_event_wrapper(forge_event: str, java_logic: str,
                               namespace: str, symbol_table=None) -> list:
        lines: list = []
        mapping = EventRouter.FORGE_TO_BEDROCK.get(forge_event)
        if mapping is None:
            lines.append(f'// TODO: No Bedrock equivalent for Forge event: {forge_event}')
            lines.append(f'// Original Java handler body preserved below as reference:')
            for line in java_logic.splitlines():
                lines.append(f'//   {line}')
            lines.append('')
            return lines

        bedrock_event, entity_param, use_before = mapping
        bus = 'beforeEvents' if use_before else 'afterEvents'

        cancel_seq = ['event', '.', 'setCanceled', '(', 'true', ')']
        if has_token_sequence(java_logic, cancel_seq) or has_token_sequence(java_logic, ['event', '.', 'isCanceled', '(', ')']):
            bus = 'beforeEvents'

        if bedrock_event == 'worldInitialize':
            lines.append(f'world.afterEvents.worldInitialize.subscribe((e) => {{')
        else:
            lines.append(f'world.{bus}.{bedrock_event}.subscribe((e) => {{')
            if entity_param:
                lines.append(f'    const {entity_param} = e.{entity_param};')

        lines.append(replace_token_sequences(java_logic, cancel_seq, 'e.cancel()'))
        lines.append('});')
        lines.append('')
        return lines

    @staticmethod
    def scan_and_emit_all_handlers(java_code: str, namespace: str,
                                   safe_name: str, symbol_table=None) -> list:
        all_lines: list = []
        unit = get_unit(java_code)
        for method in unit.methods():
            if 'SubscribeEvent' not in annotation_names(method) or len(method.parameters or []) != 1:
                continue
            event_full = type_name(method.parameters[0].type)
            event_type = event_full if event_full in EventRouter.FORGE_TO_BEDROCK else event_full.split('.')[0]
            body = unit.body_text(method)
            if body is None:
                continue
            all_lines += EventRouter.generate_event_wrapper(event_type, body.strip(), namespace, symbol_table)
        return all_lines

class MathTranspiler:
    """Java vector / math expressions -> Bedrock JS, on the AST."""

    _AXIS = {'getX': 'x', 'getY': 'y', 'getZ': 'z'}

    @staticmethod
    def _simple(n) -> Optional[str]:
        return n.member if isinstance(n, jt.MemberReference) and not n.qualifier and not n.selectors else None

    @staticmethod
    def _vector_hook(n):
        T = MathTranspiler
        if isinstance(n, tuple) and n[0] == 'sel':
            sel = n[1]
            if isinstance(sel, jt.MethodInvocation) and not sel.arguments and sel.member in T._AXIS:
                return '.' + T._AXIS[sel.member]
            return None
        if isinstance(n, jt.ClassCreator):
            args = [expr_text(a, T._vector_hook) for a in n.arguments or []]
            tname = type_name(n.type)
            tail = ''.join(_selector_text(s, T._vector_hook) for s in n.selectors or [])
            if tname == 'Vector3d' and len(args) == 3:
                return f'new Vector3({args[0]}, {args[1]}, {args[2]})' + tail
            if tname == 'AxisAlignedBB' and len(args) == 6:
                return (f'new BlockVolume({{ min: {{ x: {args[0]}, y: {args[1]}, z: {args[2]} }}, '
                        f'max: {{ x: {args[3]}, y: {args[4]}, z: {args[5]} }} }})') + tail
        if isinstance(n, jt.MethodInvocation) and n.qualifier and '.' not in n.qualifier and not n.selectors:
            if n.member in T._AXIS and not n.arguments:
                return f'{n.qualifier}.{T._AXIS[n.member]}'
            a = T._simple(n.arguments[0]) if len(n.arguments or []) == 1 else None
            q = n.qualifier
            if n.member in ('add', 'subtract') and a:
                op = '+' if n.member == 'add' else '-'
                return f'{{ x: {q}.x {op} {a}.x, y: {q}.y {op} {a}.y, z: {q}.z {op} {a}.z }}'
            if n.member == 'scale' and len(n.arguments or []) == 1:
                s = expr_text(n.arguments[0], T._vector_hook)
                return f'{{ x: {q}.x * {s}, y: {q}.y * {s}, z: {q}.z * {s} }}'
        return None

    @staticmethod
    def transpile_vector_op(java_expr: str) -> str:
        node = parse_expression(java_expr)
        if node is None:
            return java_expr
        out = expr_text(node, MathTranspiler._vector_hook)
        return out[1:-1] if out.startswith('(') and _balanced_outer(out) else out

    @staticmethod
    def _math_hook(n):
        if isinstance(n, jt.MethodInvocation) and n.qualifier == 'Math' and len(n.arguments or []) == 1 and not n.selectors:
            arg = expr_text(n.arguments[0], MathTranspiler._math_hook)
            if n.member == 'toRadians':
                return f'({arg}) * Math.PI / 180'
            if n.member == 'toDegrees':
                return f'({arg}) * 180 / Math.PI'
        return None

    @staticmethod
    def transpile_math_expr(java_expr: str) -> str:
        node = parse_expression(java_expr)
        if node is None:
            return java_expr
        out = expr_text(node, MathTranspiler._math_hook)
        return out[1:-1] if out.startswith('(') and _balanced_outer(out) else out

class JavaToBedrockMethodMap:

    STRICT_MAPPING = {

        'world.setBlockState': 'dimension.getBlock({0}).setPermutation({1})',
        'world.getBlockState': 'dimension.getBlock({0}).permutation',
        'world.getBlockEntity': 'dimension.getBlock({0})',
        'level.setBlockState': 'dimension.getBlock({0}).setPermutation({1})',

        'entity.getHealth': 'entity.getComponent("health").currentValue',
        'entity.setHealth': 'entity.getComponent("health").setCurrentValue({0})',
        'entity.getMaxHealth': 'entity.getComponent("health").maxValue',
        'entity.getPosition': 'entity.location',
        'entity.setPosition': 'entity.teleport({0})',
        'entity.getVelocity': 'entity.velocity',
        'entity.setVelocity': 'entity.applyImpulse({0})',
        'entity.kill': 'entity.kill()',

        'player.sendMessage': 'player.sendMessage({0})',
        'player.getInventory': 'player.getComponent("minecraft:inventory")',
        'player.addItem': 'player.getComponent("minecraft:inventory").container.addItem({0})',
        'player.removeItem': 'player.getComponent("minecraft:inventory").container.removeItem({0})',

        'itemStack.getTag': 'itemStack.getComponent("minecraft:enchantable")',
        'compoundTag.getInt': 'entity.getDynamicProperty({0}) || 0',
        'compoundTag.putInt': 'entity.setDynamicProperty({0}, {1})',

        'new ItemStack': 'new ItemStack({0})',
        'itemStack.getCount': 'itemStack.amount',
        'itemStack.setCount': 'itemStack.amount = {0}',

        'world.addParticle': 'dimension.spawnParticle({0}, {1})',
        'world.playSound': 'dimension.playSound({0}, {1})',
    }

    @staticmethod
    def lookup_method(java_method: str) -> Optional[str]:
        bedrock = JavaToBedrockMethodMap.STRICT_MAPPING.get(java_method)
        if bedrock is None:
            log_critical_failure(f"No Bedrock equivalent for Java method: {java_method}")
        return bedrock

    @staticmethod
    def translate_method_call(java_method: str, args: list, qualifier: Optional[str] = None) -> Optional[str]:
        template = JavaToBedrockMethodMap.lookup_method(java_method)
        if not template:
            return None

        translated_args = [translate_expression(arg) for arg in args]

        for i, arg in enumerate(translated_args):
            template = template.replace(f'{{{i}}}', arg)

        return template

class TickRegistry:

    def __init__(self):
        self.tick_handlers: Dict[str, list] = {}
        self.tick_priority: Dict[str, int] = {}

    def register_tick_handler(self, entity_id: str, logic: str, priority: int = 100):
        if entity_id not in self.tick_handlers:
            self.tick_handlers[entity_id] = []
        self.tick_handlers[entity_id].append(logic)
        self.tick_priority[entity_id] = priority

    def generate_central_tick_loop(self, namespace: str) -> list:
        lines = [
            'import { world, system } from "@minecraft/server";',
            "",
            "// Central Tick Registry - Prevents Script Watchdog Timeout",
            "const tick_registry = {",
            "    handlers: {},",
            "    max_ms_per_tick: 10, // 10ms max per tick",
            "};",
            "",
            "system.runInterval(() => {",
            "    const start_time = Date.now();",
            "    const active_entities = world.getDimension('minecraft:overworld').getEntities({",
            "        tags: ['mod:needs_tick']",
            "    });",
            "",
            "    for (const entity of active_entities) {",
            "        if (Date.now() - start_time > tick_registry.max_ms_per_tick) break;",
            "        const handler_id = entity.typeId;",
            "        if (tick_registry.handlers[handler_id]) {",
            "            try {",
            "                tick_registry.handlers[handler_id](entity);",
            "            } catch (e) {",
            "                console.error(`Tick error for ${handler_id}: ${e.message}`);",
            "            }",
            "        }",
            "    }",
            "}, 1); // Run every game tick",
            "",
        ]

        for entity_id, handlers in self.tick_handlers.items():
            lines.append(f"tick_registry.handlers['{namespace}:{entity_id}'] = (entity) => {{")
            for handler in handlers:
                lines.extend(handler.split('\n'))
            lines.append("};")
            lines.append("")

        return lines

class ComponentUIBridge:

    @staticmethod
    def detect_container_class(java_code: str) -> Optional[Dict[str, str]]:
        unit = get_unit(java_code)
        container_info = {'class_name': '', 'slots': [], 'buttons': [], 'fields': []}
        for cls in unit.classes():
            if cls.extends is not None and simple_type_name(cls.extends) == 'Container':
                container_info['class_name'] = cls.name
                break
        for call in unit.walk(jt.MethodInvocation):
            if call.member == 'addSlotToContainer' and call.qualifier in (None, '', 'this') or \
                    (call.member == 'addSlotToContainer' and not call.qualifier):
                for arg in call.arguments or []:
                    if isinstance(arg, jt.ClassCreator) and simple_type_name(arg.type) == 'Slot':
                        container_info['slots'].append(expr_text(arg.arguments))
        for node in unit.walk(jt.ClassCreator):
            args = node.arguments or []
            if simple_type_name(node.type) == 'GuiButton' and len(args) >= 4 and string_value(args[3]) is not None:
                container_info['buttons'].append({
                    'id': literal_text(args[0]) or expr_text(args[0]),
                    'x': expr_text(args[1]),
                    'y': expr_text(args[2]),
                    'label': string_value(args[3]),
                })
        return container_info if container_info['class_name'] else None

    @staticmethod
    def generate_action_form(container_info: Dict[str, str]) -> list:
        lines = [
            f"// Container → UI Form: {container_info['class_name']}",
            "const show_container_form = async (player) => {",
            "    const form = new ActionFormData();",
            f"    form.title('{container_info['class_name']}');",
            "    ",
        ]

        for button in container_info.get('buttons', []):
            lines.append(f"    form.button('{button['label']}');")

        lines.extend([
            "    ",
            "    const response = await form.show(player);",
            "    if (response.canceled) return;",
            "    ",
            "    switch (response.selection) {",
        ])

        for i, button in enumerate(container_info.get('buttons', [])):
            lines.append(f"        case {i}:")
            lines.append(f"            handle_container_action_{button['id']}(player);")
            lines.append(f"            break;")

        lines.extend([
            "    }",
            "};",
            "",
        ])

        return lines

class JavaGUIConverter:

    _GUI_BASES = {
        'Screen', 'AbstractContainerScreen', 'GuiScreen',
        'AbstractGui', 'ContainerScreen', 'ChestScreen',
    }

    @staticmethod
    def is_gui_class(java_code: str) -> bool:
        return any(c.extends is not None and simple_type_name(c.extends) in JavaGUIConverter._GUI_BASES
                   for c in get_unit(java_code).classes())

    @staticmethod
    def _int_assignments(unit: JavaUnit) -> Dict[str, int]:
        """First integer value assigned to each simple name (field initialisers and `x = N` / `this.x = N`)."""
        out: Dict[str, int] = {}

        def put(name, value):
            num = number_value(value)
            if name and num is not None and name not in out:
                out[name] = int(num)
        for f in unit.fields():
            for d in f.declarators:
                put(d.name, d.initializer)
        for a in unit.walk(jt.Assignment):
            lhs = a.expressionl
            if isinstance(lhs, jt.MemberReference):
                put(lhs.member, a.value)
            elif isinstance(lhs, jt.This) and lhs.selectors and isinstance(lhs.selectors[0], jt.MemberReference):
                put(lhs.selectors[0].member, a.value)
        return out

    @staticmethod
    def extract_gui_info(java_code: str) -> Dict:
        unit = get_unit(java_code)
        info: Dict = {
            'class_name': '', 'title': None, 'width': 176, 'height': 166,
            'slots': [], 'buttons': [], 'labels': [], 'text_fields': [],
        }
        for cls in unit.classes():
            if cls.extends is not None:
                info['class_name'] = cls.name
                break

        ints = JavaGUIConverter._int_assignments(unit)
        for key in ('imageWidth', 'xSize'):
            if key in ints:
                info['width'] = ints[key]
                break
        for key in ('imageHeight', 'ySize'):
            if key in ints:
                info['height'] = ints[key]
                break

        def translatable(node):
            for call in iter_calls(node):
                if call.member == 'translatable' and call.qualifier == 'Component' and call.arguments:
                    s = string_value(call.arguments[0])
                    if s is not None:
                        return s
            return None

        for sup in unit.walk(jt.SuperConstructorInvocation):
            title = translatable(list(sup.arguments or []))
            if title is not None:
                info['title'] = title
                break

        def ints_of(args):
            vals = [number_value(a) for a in args]
            return None if any(v is None for v in vals) else [int(v) for v in vals]

        for call in unit.walk(jt.MethodInvocation):
            args = call.arguments or []
            if call.member == 'addSlot' and args and isinstance(args[0], jt.ClassCreator) \
                    and simple_type_name(args[0].type).endswith('Slot'):
                vals = ints_of((args[0].arguments or [])[1:4]) if len(args[0].arguments or []) == 4 else None
                if vals:
                    info['slots'].append({'index': vals[0], 'x': vals[1], 'y': vals[2]})
            elif call.member == 'addRenderableWidget':
                for inner in iter_calls(list(args)):
                    if inner.member == 'builder' and (inner.qualifier or '').split('.')[-1] == 'Button':
                        label = translatable(list(inner.arguments or []))
                        if label is not None:
                            info['buttons'].append({'label': label, 'x': 0, 'y': 0, 'w': 80, 'h': 20})
                            break
            elif call.member == 'addButton' and args and isinstance(args[0], jt.ClassCreator) \
                    and simple_type_name(args[0].type).endswith('Button'):
                cargs = args[0].arguments or []
                vals = ints_of(cargs[:4]) if len(cargs) >= 5 else None
                label = string_value(cargs[4]) if len(cargs) >= 5 else None
                if vals and label is not None:
                    info['buttons'].append({'label': label, 'x': vals[0], 'y': vals[1], 'w': vals[2], 'h': vals[3]})
            elif call.member == 'drawString' and len(args) >= 2:
                text = string_value(args[1])
                if text is not None:
                    info['labels'].append({'text': text})
        for node in unit.walk(jt.ClassCreator):
            if simple_type_name(node.type) in ('EditBox', 'TextField'):
                info['text_fields'].append({'hint': 'text_input'})
        return info

    @staticmethod
    def generate_variables_grid_json(gui_info: Dict, namespace: str) -> dict:
        slots = gui_info.get('slots', [])
        if not slots:
            return {}
        grid_items = []
        for slot in slots:
            grid_items.append({
                'type': 'slot',
                'index': slot['index'],
                'offset': [slot['x'] - gui_info['width'] // 2,
                             slot['y'] - gui_info['height'] // 2, 0],
            })
        return {
            'namespace': namespace,
            f'{gui_info["class_name"]}_grid': {
                'type':   'grid',
                'grid_dimensions': {'x': len(slots), 'y': 1},
                'grid_item_template': f'{namespace}.{gui_info["class_name"]}_slot',
                'collection_name': f'{namespace}_container',
                'grid_rescaling_type': 'none',
                '__items__': grid_items,
            },
        }

    @staticmethod
    def generate_controls_json(gui_info: Dict, namespace: str) -> dict:
        controls = {}
        for i, btn in enumerate(gui_info.get('buttons', [])):
            key = f'{sanitize_identifier(btn["label"])}_button_{i}'
            controls[key] = {
                'type':        'button',
                'text':        btn['label'],
                'size':        [btn['w'], btn['h']],
                'offset':      [btn['x'], btn['y']],
                'button_mappings': [],
            }
        for i, lbl in enumerate(gui_info.get('labels', [])):
            key = f'label_{i}'
            controls[key] = {
                'type':    'label',
                'text':    lbl['text'],
                'offset':  [0, i * 10],
                'color':   [0.2, 0.2, 0.2, 1.0],
            }
        return {'namespace': namespace, 'controls': controls}

    @staticmethod
    def generate_modal_form_js(gui_info: Dict, namespace: str) -> list:
        cls = gui_info['class_name']
        safe = clean_java_artifact_name(cls)
        title = gui_info.get('title') or cls
        lines = [
            f'// GUI Form: {cls} → Bedrock ModalFormData',
            f'import {{ world }} from "@minecraft/server";',
            f'import {{ ModalFormData, ActionFormData }} from "@minecraft/server-ui";',
            '',
            f'export async function show_{safe}_form(player) {{',
        ]
        has_text = bool(gui_info.get('text_fields'))
        has_btns = bool(gui_info.get('buttons'))
        if has_text:
            lines.append(f'    const form = new ModalFormData();')
            lines.append(f'    form.title("{title}");')
            for i, tf in enumerate(gui_info['text_fields']):
                lines.append(f'    form.textField("Field {i}", "Enter value");')
            lines += [
                f'    const res = await form.show(player);',
                f'    if (res.canceled) return;',
                f'    const [{ ", ".join(f"field{i}" for i in range(len(gui_info["text_fields"]))) }] = res.formValues;',
            ]
        elif has_btns:
            lines.append(f'    const form = new ActionFormData();')
            lines.append(f'    form.title("{title}");')
            for btn in gui_info['buttons']:
                lines.append(f'    form.button("{btn["label"]}");')
            lines += [
                f'    const res = await form.show(player);',
                f'    if (res.canceled) return;',
                f'    switch (res.selection) {{',
            ]
            for i, btn in enumerate(gui_info['buttons']):
                fn = sanitize_identifier(btn['label'])
                lines += [
                    f'        case {i}: handle_{safe}_{fn}(player); break;',
                ]
            lines.append(f'    }}')
        else:
            lines.append(f'    // No interactive components detected — check PORTING_NOTES')
        lines += [f'}}', '']
        return lines

    @staticmethod
    def process(java_code: str, namespace: str, out_rp: str, out_bp_scripts: str) -> None:
        if not JavaGUIConverter.is_gui_class(java_code):
            return
        gui_info = JavaGUIConverter.extract_gui_info(java_code)
        if not gui_info['class_name']:
            return
        safe = sanitize_identifier(gui_info['class_name'])

        grid = JavaGUIConverter.generate_variables_grid_json(gui_info, namespace)
        if grid:
            grid_path = os.path.join(out_rp, 'ui', f'{safe}_grid.json')
            os.makedirs(os.path.dirname(grid_path), exist_ok=True)
            with open(grid_path, 'w', encoding='utf-8') as fh:
                json.dump(grid, fh, indent=2)

        ctrl = JavaGUIConverter.generate_controls_json(gui_info, namespace)
        ctrl_path = os.path.join(out_rp, 'ui', f'{safe}_controls.json')
        os.makedirs(os.path.dirname(ctrl_path), exist_ok=True)
        with open(ctrl_path, 'w', encoding='utf-8') as fh:
            json.dump(ctrl, fh, indent=2)

        js_lines = JavaGUIConverter.generate_modal_form_js(gui_info, namespace)
        js_path = os.path.join(out_bp_scripts, f'ui_{safe}.js')
        os.makedirs(os.path.dirname(js_path), exist_ok=True)
        with open(js_path, 'w', encoding='utf-8') as fh:
            fh.write('\n'.join(js_lines))

class DependencyRegistry:

    def __init__(self):
        self.scripts: Dict[str, Dict] = {}
        self.nbt_properties: Dict[str, Set[str]] = {}
        self.dynamic_props: Dict[str, Set[str]] = {}
        self.tick_entities: Set[str] = set()
        self.capabilities: Dict[str, str] = {}

    def register_script(self, script_id: str, depends_on: List[str] = None):
        self.scripts[script_id] = {'depends_on': depends_on or [], 'code': ''}

    def register_nbt_property(self, entity_id: str, prop_name: str):
        if entity_id not in self.nbt_properties:
            self.nbt_properties[entity_id] = set()
        self.nbt_properties[entity_id].add(prop_name)

    def register_dynamic_property(self, entity_id: str, prop_name: str):
        if entity_id not in self.dynamic_props:
            self.dynamic_props[entity_id] = set()
        self.dynamic_props[entity_id].add(prop_name)

    def mark_entity_for_ticking(self, entity_id: str):
        self.tick_entities.add(entity_id)

class GlobalCapabilityRegistry:

    @staticmethod
    def generate_registry_js(namespace: str) -> list:
        return [
            'import { world } from "@minecraft/server";',
            '',
            '// ── Global Capability Registry ────────────────────────────────────────────',
            '// Shared by all converted mods in this pack.  Keys are namespaced to avoid',
            '// collisions when multiple mods are loaded side-by-side.',
            '',
            'const _ns = (ns, key) => `${ns}:cap_${key}`;',
            '',
            'export const CapRegistry = {',
            '',
            '    // ── Energy ──────────────────────────────────────────────────────────',
            '    energy: {',
            '        /** Receive energy into entity up to maxCapacity RF/FE.  Returns accepted amount. */',
            '        receive(entity, ns, amount, maxCapacity = 1_000_000) {',
            '            const key = _ns(ns, "energy");',
            '            const cur = entity.getDynamicProperty(key) ?? 0;',
            '            const accepted = Math.min(amount, maxCapacity - cur);',
            '            entity.setDynamicProperty(key, cur + accepted);',
            '            return accepted;',
            '        },',
            '        extract(entity, ns, amount) {',
            '            const key = _ns(ns, "energy");',
            '            const cur = entity.getDynamicProperty(key) ?? 0;',
            '            const extracted = Math.min(amount, cur);',
            '            entity.setDynamicProperty(key, cur - extracted);',
            '            return extracted;',
            '        },',
            '        get(entity, ns) { return entity.getDynamicProperty(_ns(ns,"energy")) ?? 0; },',
            '        set(entity, ns, v){ entity.setDynamicProperty(_ns(ns,"energy"), v); },',
            '    },',
            '',
            '    // ── Fluid ───────────────────────────────────────────────────────────',
            '    fluid: {',
            '        fill(entity, ns, fluidId, amount, capacity = 1000) {',
            '            const amtKey  = _ns(ns, "fluid_amount");',
            '            const typeKey = _ns(ns, "fluid_type");',
            '            const curAmt  = entity.getDynamicProperty(amtKey) ?? 0;',
            '            const curType = entity.getDynamicProperty(typeKey) ?? fluidId;',
            '            if (curAmt > 0 && curType !== fluidId) return 0;',
            '            const filled  = Math.min(amount, capacity - curAmt);',
            '            entity.setDynamicProperty(amtKey,  curAmt + filled);',
            '            entity.setDynamicProperty(typeKey, fluidId);',
            '            return filled;',
            '        },',
            '        drain(entity, ns, amount) {',
            '            const amtKey = _ns(ns, "fluid_amount");',
            '            const cur    = entity.getDynamicProperty(amtKey) ?? 0;',
            '            const drained = Math.min(amount, cur);',
            '            entity.setDynamicProperty(amtKey, cur - drained);',
            '            return { amount: drained, type: entity.getDynamicProperty(_ns(ns,"fluid_type")) ?? "minecraft:water" };',
            '        },',
            '        getAmount(entity, ns){ return entity.getDynamicProperty(_ns(ns,"fluid_amount")) ?? 0; },',
            '        getType(entity,   ns){ return entity.getDynamicProperty(_ns(ns,"fluid_type"))   ?? "minecraft:water"; },',
            '    },',
            '',
            '    // ── Item slots ──────────────────────────────────────────────────────',
            '    item: {',
            '        /** Insert an ItemStack JSON into a named slot. */',
            '        insert(entity, ns, slotIndex, itemJson) {',
            '            const key = _ns(ns, `item_slot_${slotIndex}`);',
            '            if (entity.getDynamicProperty(key)) return false;',
            '            entity.setDynamicProperty(key, JSON.stringify(itemJson));',
            '            return true;',
            '        },',
            '        extract(entity, ns, slotIndex) {',
            '            const key  = _ns(ns, `item_slot_${slotIndex}`);',
            '            const raw  = entity.getDynamicProperty(key);',
            '            if (!raw) return null;',
            '            entity.setDynamicProperty(key, undefined);',
            '            return JSON.parse(raw);',
            '        },',
            '        get(entity, ns, slotIndex) {',
            '            const raw = entity.getDynamicProperty(_ns(ns,`item_slot_${slotIndex}`));',
            '            return raw ? JSON.parse(raw) : null;',
            '        },',
            '    },',
            '',
            '    // ── Cross-mod DynamicProperty sharing ──────────────────────────────',
            '    data: {',
            '        get(entity, ns, key)      { return entity.getDynamicProperty(_ns(ns, key)); },',
            '        set(entity, ns, key, val) { entity.setDynamicProperty(_ns(ns, key), val); },',
            '        has(entity, ns, key)      { return entity.getDynamicProperty(_ns(ns, key)) !== undefined; },',
            '        del(entity, ns, key)      { entity.setDynamicProperty(_ns(ns, key), undefined); },',
            '    },',
            '',
            '    // Dynamic properties (energy, fluid_amount, fluid_type) need no registration in @minecraft/server 1.13.',
            '',
            '};  // end CapRegistry',
            '',
        ]

    @staticmethod
    def write(namespace: str, bp_folder: str) -> None:
        out_path = os.path.join(bp_folder, 'scripts', 'cap_registry.js')
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, 'w', encoding='utf-8') as fh:
            fh.write('\n'.join(GlobalCapabilityRegistry.generate_registry_js(namespace)))

    @staticmethod
    def ensure_import_in_main(bp_folder: str) -> None:
        main_path = os.path.join(bp_folder, 'scripts', 'main.js')
        import_line = 'import "./cap_registry.js";\n'
        if os.path.exists(main_path):
            with open(main_path, 'r', encoding='utf-8') as fh:
                content = fh.read()
            if 'cap_registry' not in content:
                with open(main_path, 'w', encoding='utf-8') as fh:
                    fh.write(import_line + content)
        else:
            with open(main_path, 'w', encoding='utf-8') as fh:
                fh.write(import_line)
def read_all_java_files(root_dir=".") -> Dict[str, str]:
    if root_dir == "." and STATE.deobfuscated_java_files:
        return dict(STATE.deobfuscated_java_files)

    java_files = {}
    skip_dir = os.path.normpath(OUTPUT_DIR)
    for root, dirs, files in os.walk(root_dir):
        norm_root = os.path.normpath(root)
        dirs[:] = [
            d for d in dirs
            if not os.path.normpath(os.path.join(norm_root, d)).startswith(skip_dir + os.sep)
            and os.path.normpath(os.path.join(norm_root, d)) != skip_dir
        ]
        for f in files:
            if f.endswith(".java"):
                path = os.path.join(root, f)
                try:
                    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                        java_files[path] = fh.read()
                except Exception:
                    continue
    return java_files

def _is_probably_obfuscated_java_name(name: Optional[str]) -> bool:
    if not name:
        return True
    n = ''.join(ch for ch in str(name) if (ch.isascii() and ch.isalnum()) or ch in '_$')
    if not n or len(n) <= 2:
        return True
    letters = sum(ch.isalpha() for ch in n)
    if n.isdigit() or (letters == 1 and n.replace('_', '').isalnum() and '_' not in n and n[0].isalpha() and n[1:].isdigit()):
        return True
    if n.startswith(('func_', 'field_', 'm_', 'f_', 'lambda$', 'access$')):
        return True
    return False

def _infer_java_class_role(java_code: str, filename: str = '', cls_name: Optional[str] = None) -> str:
    haystack = ' '.join([
        str(cls_name or ''),
        os.path.basename(filename) or '',
        os.path.splitext(os.path.basename(filename))[0] if filename else '',
        java_code[:1600] if java_code else '',
    ]).lower()

    role_checks = [
        ('renderer', ['renderer', 'geomodel', 'geoentityrenderer', 'blockentityrenderer', 'layerrenderer']),
        ('model', ['model', 'layerdefinition', 'meshdefinition', 'modelpart', 'createbodylayer', 'getmodelresource']),
        ('screen', ['screen', 'gui', 'abstractcontainerscreen', 'container screen', 'chestscreen']),
        ('entity', ['extends entity', 'livingentity', 'mob', 'animal', 'monster', 'projectile', 'entitytype']),
        ('block', ['extends block', 'blockstate', 'blockentity', 'tileentity', 'createblockstatedefinition']),
        ('item', ['extends item', 'itemstack', 'useon', 'inventorytick']),
        ('goal', ['goal', 'pathfind', 'targetselector', 'setmutexbits']),
        ('event_handler', ['@subscribeevent', 'eventbus', 'forgeevent', 'clienttickevent', 'servertickevent']),
        ('mixin', ['@mixin', 'inject(', 'redirect(', 'overwrite(', 'accessor(', 'invoker(']),
        ('registry', ['deferredregister', 'registryobject', 'registerevent', 'bootstrapcontext']),
        ('capability', ['capability', 'ifluidhandler', 'ienergystorage', 'iitemhandler']),
        ('packet', ['packet', 'network', 'friendlybytebuf', 'serverbound', 'clientbound']),
    ]

    for role, needles in role_checks:
        if any(n in haystack for n in needles):
            return role

    if '@mod(' in haystack or 'modid' in haystack or 'fabric.mod.json' in haystack:
        return 'mod_main'

    return 'class'

def _unique_java_name(base: str, used: Set[str]) -> str:
    candidate = sanitize_identifier(base) or "class"
    if candidate not in used:
        used.add(candidate)
        return candidate
    index = 2
    while f"{candidate}_{index}" in used:
        index += 1
    candidate = f"{candidate}_{index}"
    used.add(candidate)
    return candidate

def _semantic_member_name(role: str, kind: str, member_name: str, return_type: str = '', param_types: Optional[List[str]] = None, java_code: str = '') -> Optional[str]:
    if not _is_probably_obfuscated_java_name(member_name):
        return None

    lowered = (java_code or '').lower()
    return_type = (return_type or '').strip()

    if kind == 'method':
        if role == 'renderer':
            if 'resourcelocation' in return_type.lower():
                if 'texture' in lowered:
                    return 'getTextureLocation'
                if 'model' in lowered or 'geo' in lowered:
                    return 'getModelResource'
            if return_type.lower() == 'void':
                return 'render'
        elif role == 'model':
            if 'layerdefinition' in return_type.lower():
                return 'createBodyLayer'
            if return_type.lower() == 'void' and len(param_types or []) >= 4:
                return 'setupAnim'
        elif role == 'screen':
            if return_type.lower() == 'void':
                if 'init' in lowered:
                    return 'init'
                return 'render'
        elif role == 'registry':
            return 'register'
        elif role == 'event_handler':
            return 'handleEvent'
        elif role == 'goal':
            return 'tick'
        elif role == 'entity':
            if return_type.lower() == 'void':
                return 'tick'
        elif role == 'packet':
            if return_type.lower() == 'void':
                if 'encode' in lowered:
                    return 'encode'
                if 'decode' in lowered:
                    return 'decode'
                return 'handlePacket'
        return f"{role}_method"

    if kind == 'field':
        if role == 'renderer':
            return 'render_state'
        if role == 'model':
            return 'model_state'
        if role == 'entity':
            return 'entity_state'
        return f"{role}_field"

    return None

def _rewrite_java_identifiers(source: str, rename_map: Dict[str, str]) -> str:
    rename_map = {k: v for k, v in (rename_map or {}).items() if k and v and k != v}
    return rename_identifiers(source, rename_map) if rename_map else source

def extract_class_name(java_code: str) -> Optional[str]:
    return JavaAST(java_code).primary_class_name()
def _strip_geo_ext(base: str) -> str:
    low = base.lower()
    for ext in ('.geo.json', '.geo'):
        if low.endswith(ext):
            return base[:-len(ext)]
    return base

def _is_geo_path(path: str) -> bool:
    return 'geo/' in path or path.endswith('.geo.json') or path.endswith('.geo')

def find_model_geometry_in_code(java_code: str) -> Optional[Tuple[Optional[str], str]]:
    ast = JavaAST(java_code)
    for node in ast.object_creations_of('ResourceLocation'):
        args = node.arguments or []
        ns_val, path_val = None, None
        if len(args) >= 2:
            ns_val, path_val = string_value(args[0]), string_value(args[1])
        elif len(args) == 1:
            raw = string_value(args[0])
            if raw is not None:
                ns_val, path_val = raw.split(':', 1) if ':' in raw else (None, raw)
        if path_val and _is_geo_path(path_val):
            name = _strip_geo_ext(os.path.basename(path_val))
            return (ns_val.lower() if ns_val else None, sanitize_identifier(name))
    for lit in ast.all_string_literals():
        if _is_geo_path(lit):
            ns, path = lit.split(':', 1) if ':' in lit else (None, lit)
            name = _strip_geo_ext(os.path.basename(path))
            return (ns.lower() if ns else None, sanitize_identifier(name))
    return None
def _parse_java_float(s: str) -> Optional[float]:
    if s is None:
        return None
    cleaned = str(s).strip()
    if cleaned and cleaned[-1] in 'DdFfLl':
        cleaned = cleaned[:-1]
    try:
        return float(cleaned)
    except (ValueError, TypeError):
        return None

JAVA_PARTICLE_MAP = {
    "explosion": "minecraft:explosion_particle",
    "large_explosion": "minecraft:explosion_particle",
    "huge_explosion": "minecraft:explosion_particle",
    "fireworks_spark": "minecraft:fireworks_spark_particle",
    "bubble": "minecraft:bubble_particle",
    "splash": "minecraft:water_splash_particle",
    "wake": "minecraft:water_wake_particle",
    "suspended": "minecraft:water_splash_particle",
    "depth_suspend": "minecraft:water_splash_particle",
    "crit": "minecraft:critical_hit_emitter",
    "magic_crit": "minecraft:critical_hit_emitter",
    "smoke": "minecraft:basic_smoke_particle",
    "large_smoke": "minecraft:basic_smoke_particle",
    "mob_spell": "minecraft:spell_particle",
    "mob_spell_ambient": "minecraft:spell_particle",
    "spell": "minecraft:spell_particle",
    "instant_spell": "minecraft:spell_particle",
    "witch_magic": "minecraft:witch_spell_particle",
    "note": "minecraft:note_particle",
    "portal": "minecraft:portal_particle",
    "enchantment_table": "minecraft:enchanting_table_particle",
    "flame": "minecraft:basic_flame_particle",
    "lava": "minecraft:lava_particle",
    "footstep": "minecraft:falling_dust_sand_particle",
    "cloud": "minecraft:evaporation_particle",
    "reddust": "minecraft:redstone_wire_dust_particle",
    "snowball": "minecraft:snowball_particle",
    "drip_water": "minecraft:water_drip_particle",
    "drip_lava": "minecraft:lava_drip_particle",
    "snow_shovel": "minecraft:snowball_particle",
    "slime": "minecraft:slime_particle",
    "heart": "minecraft:heart_particle",
    "angry_villager": "minecraft:villager_angry_particle",
    "happy_villager": "minecraft:villager_happy_particle",
    "barrier": "minecraft:barrier_particle",
    "item_crack": "minecraft:basic_crit_particle",
    "block_crack": "minecraft:falling_dust_sand_particle",
    "block_dust": "minecraft:falling_dust_sand_particle",
    "droplet": "minecraft:water_drip_particle",
    "take": "minecraft:basic_crit_particle",
    "mob_appearance": "minecraft:elder_guardian_particle",
    "dragon_breath": "minecraft:dragon_breath_particle",
    "end_rod": "minecraft:end_rod_particle",
    "damage_indicator": "minecraft:critical_hit_emitter",
    "sweep_attack": "minecraft:critical_hit_emitter",
    "totem": "minecraft:totem_particle",
    "spit": "minecraft:llama_spit_particle",
    "squid_ink": "minecraft:squid_ink_particle",
    "bubble_pop": "minecraft:bubble_pop_particle",
    "current_down": "minecraft:bubble_particle",
    "bubble_column_up": "minecraft:bubble_particle",
    "nautilus": "minecraft:nautilus_particle",
    "dolphin": "minecraft:dolphin_particle",
    "campfire_cosy_smoke": "minecraft:campfire_smoke_particle",
    "campfire_signal_smoke": "minecraft:campfire_smoke_particle",
    "composter": "minecraft:composter_particle",
    "flash": "minecraft:flash_particle",
    "falling_lava": "minecraft:lava_drip_particle",
    "landing_lava": "minecraft:lava_particle",
    "falling_water": "minecraft:water_drip_particle",
    "dust": "minecraft:redstone_wire_dust_particle",
    "item_snowball": "minecraft:snowball_particle",
    "item_slime": "minecraft:slime_particle",
    "item_squid_ink": "minecraft:squid_ink_particle",
    "item_bubble_pop": "minecraft:bubble_pop_particle",
    "item_current_down": "minecraft:bubble_particle",
    "item_bubble_column_up": "minecraft:bubble_particle",
    "item_nautilus": "minecraft:nautilus_particle",
    "item_dolphin": "minecraft:dolphin_particle",
    "item_campfire_cosy_smoke": "minecraft:campfire_smoke_particle",
    "item_campfire_signal_smoke": "minecraft:campfire_smoke_particle",
    "item_composter": "minecraft:composter_particle",
    "item_flash": "minecraft:flash_particle",
    "item_falling_lava": "minecraft:lava_drip_particle",
    "item_landing_lava": "minecraft:lava_particle",
    "item_falling_water": "minecraft:water_drip_particle",
    "soul_fire_flame": "minecraft:soul_particle",
    "soul": "minecraft:soul_particle",
    "ash": "minecraft:basic_smoke_particle",
    "crimson_spore": "minecraft:crimson_spore_particle",
    "warped_spore": "minecraft:warped_spore_particle",
    "soul_fire_flame": "minecraft:soul_particle",
    "dripping_obsidian_tear": "minecraft:obsidian_tear_particle",
    "falling_obsidian_tear": "minecraft:obsidian_tear_particle",
    "landing_obsidian_tear": "minecraft:obsidian_tear_particle",
    "reverse_portal": "minecraft:portal_particle",
    "white_ash": "minecraft:basic_smoke_particle",
    "light": "minecraft:light_particle",
    "dust_color_transition": "minecraft:redstone_wire_dust_particle",
    "vibration": "minecraft:vibration_particle",
    "falling_spore_blossom": "minecraft:spore_blossom_particle",
    "spore_blossom_air": "minecraft:spore_blossom_particle",
    "small_flame": "minecraft:basic_flame_particle",
    "snowflake": "minecraft:snowball_particle",
    "dripping_dripstone_lava": "minecraft:lava_drip_particle",
    "falling_dripstone_lava": "minecraft:lava_drip_particle",
    "dripping_dripstone_water": "minecraft:water_drip_particle",
    "falling_dripstone_water": "minecraft:water_drip_particle",
    "glow_squid_ink": "minecraft:squid_ink_particle",
    "glow": "minecraft:glow_particle",
    "wax_on": "minecraft:wax_particle",
    "wax_off": "minecraft:wax_particle",
    "electric_spark": "minecraft:electric_spark_particle",
    "scrape": "minecraft:scrape_particle",
    "shriek": "minecraft:shriek_particle",
    "sonic_boom": "minecraft:sonic_boom_particle",
    "sculk_soul": "minecraft:soul_particle",
    "sculk_charge": "minecraft:sculk_charge_particle",
    "sculk_charge_pop": "minecraft:sculk_charge_pop_particle",
    "sonic_explosion": "minecraft:sonic_boom_particle",
    "dust_plume": "minecraft:dust_plume_particle",
    "gust": "minecraft:gust_particle",
    "trial_spawner_detection": "minecraft:trial_spawner_detection_particle",
    "trial_spawner_detection_ominous": "minecraft:trial_spawner_detection_ominous_particle",
    "vault_connection": "minecraft:vault_connection_particle",
    "dust_pillar": "minecraft:dust_pillar_particle",
    "ominous_spawning": "minecraft:ominous_spawning_particle",
    "raid_omen": "minecraft:raid_omen_particle",
    "trial_omen": "minecraft:trial_omen_particle",
}

_EFFECT_NAME_MAP = {

    "MOVEMENT_SPEED": "speed", "SPEED": "speed", "MOVEMENT_SLOWDOWN": "slowness", "SLOWNESS": "slowness",
    "DIG_SPEED": "haste", "HASTE": "haste", "DIG_SLOWDOWN": "mining_fatigue", "MINING_FATIGUE": "mining_fatigue",
    "DAMAGE_BOOST": "strength", "STRENGTH": "strength", "HEAL": "instant_health", "INSTANT_HEALTH": "instant_health",
    "HARM": "instant_damage", "INSTANT_DAMAGE": "instant_damage", "JUMP": "jump_boost", "JUMP_BOOST": "jump_boost",
    "CONFUSION": "nausea", "NAUSEA": "nausea", "REGENERATION": "regeneration",
    "DAMAGE_RESISTANCE": "resistance", "RESISTANCE": "resistance", "FIRE_RESISTANCE": "fire_resistance",
    "WATER_BREATHING": "water_breathing", "INVISIBILITY": "invisibility", "BLINDNESS": "blindness",
    "NIGHT_VISION": "night_vision", "HUNGER": "hunger", "WEAKNESS": "weakness", "POISON": "poison", "WITHER": "wither",
    "HEALTH_BOOST": "health_boost", "ABSORPTION": "absorption", "SATURATION": "saturation", "LEVITATION": "levitation",
    "SLOW_FALLING": "slow_falling", "CONDUIT_POWER": "conduit_power", "BAD_OMEN": "bad_omen",
    "HERO_OF_THE_VILLAGE": "village_hero", "DARKNESS": "darkness",
}

_BEDROCK_IDS: Optional[Dict[str, Set[str]]] = None

def bedrock_ids(kind: str) -> Optional[Set[str]]:
    """Valid Bedrock ids ('effects' | 'entities' | 'blocks' | 'items'), or None if data/bedrock_ids.json is missing."""
    global _BEDROCK_IDS
    if _BEDROCK_IDS is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'bedrock_ids.json')
        try:
            raw = json.load(open(path, encoding='utf-8'))
            _BEDROCK_IDS = {k: set(v) for k, v in raw.items() if isinstance(v, list)}
        except (OSError, ValueError):
            _BEDROCK_IDS = {}
    return _BEDROCK_IDS.get(kind)

_SOUND_NAME_MAP = {
    "EXPERIENCE_ORB_PICKUP": "random.orb", "ITEM_PICKUP": "random.pop",
    "ENTITY_PLAYER_LEVELUP": "random.levelup", "ENTITY_PLAYER_HURT": "game.player.hurt",
    "ENTITY_PLAYER_DEATH": "game.player.die", "BLOCK_GLASS_BREAK": "random.glass",
    "ENTITY_GENERIC_EXPLODE": "random.explode", "ENTITY_LIGHTNING_BOLT_THUNDER": "ambient.weather.thunder",
    "ENTITY_ENDER_DRAGON_GROWL": "mob.enderdragon.growl", "BLOCK_ANVIL_USE": "random.anvil_use",
    "ENTITY_ARROW_SHOOT": "random.bow", "ENTITY_FIREWORK_ROCKET_LAUNCH": "firework.launch",
    "AMBIENT_CAVE": "ambient.cave",
}

_FORGE_EVENT_MAP = {
    "PlayerInteractEvent.EntityInteract":  ("afterEvents", "playerInteractWithEntity"),
    "PlayerInteractEvent.RightClickBlock": ("afterEvents", "playerInteractWithBlock"),
    "PlayerInteractEvent.RightClickItem":  ("afterEvents", "useItem"),
    "PlayerInteractEvent.LeftClickBlock":  ("afterEvents", "playerBreakBlock"),
    "LivingHurtEvent":                     ("afterEvents", "entityHurt"),
    "LivingDeathEvent":                    ("afterEvents", "entityDie"),
    "BlockEvent.BreakEvent":               ("afterEvents", "playerBreakBlock"),
    "BlockEvent.PlaceEvent":               ("afterEvents", "playerPlaceBlock"),
    "EntityJoinLevelEvent":                ("afterEvents", "entitySpawn"),
    "ItemCraftedEvent":                    ("afterEvents", "crafted"),
    "PlayerEvent.ItemPickupEvent":         ("afterEvents", "itemStartPickUp"),
}

def _find_static_event_handlers(java_code: str) -> list:
    handlers = []
    seen = set()
    unit = as_unit(java_code)
    methods = [m for m in unit.methods() if 'static' in (m.modifiers or ())]
    for event_type, (phase, bedrock_event) in _FORGE_EVENT_MAP.items():
        short = event_type.split(".")[-1]
        for m in methods:
            if m.name in seen:
                continue
            param = next((p for p in m.parameters or [] if simple_type_name(p.type) == short), None)
            if param is None:
                continue
            seen.add(m.name)
            text = unit.body_text(m)
            body = '{' + text + '}' if text is not None else list(m.body or [])
            if body:
                handlers.append((m.name, event_type, phase, bedrock_event, param.name, body))
    return handlers

def _extract_tag_path(java_code, field_name: str) -> Optional[str]:
    unit = as_unit(java_code)
    factories = {'fromNamespaceAndPath', 'of', 'parse', 'withDefaultNamespace'}
    for f in unit.fields():
        for d in f.declarators:
            init = d.initializer
            if d.name != field_name or not isinstance(init, jt.MethodInvocation):
                continue
            if init.member != 'create' or init.qualifier != 'TagKey' or len(init.arguments or []) < 2:
                continue
            loc = init.arguments[1]
            if isinstance(loc, jt.MethodInvocation) and loc.qualifier == 'ResourceLocation' \
                    and loc.member in factories and len(loc.arguments or []) >= 2:
                return string_value(loc.arguments[1])
            if isinstance(loc, jt.ClassCreator) and simple_type_name(loc.type) == 'ResourceLocation' \
                    and len(loc.arguments or []) >= 2:
                return string_value(loc.arguments[1])
    return None

def _get_player_var(event_type: str, param: str) -> str:
    if not event_type:
        return param
    player_events = {
        "PlayerInteractEvent.EntityInteract",
        "PlayerInteractEvent.RightClickBlock",
        "PlayerInteractEvent.RightClickItem",
        "PlayerInteractEvent.LeftClickBlock",
        "ItemCraftedEvent",
        "PlayerEvent.ItemPickupEvent",
    }
    if event_type in player_events:
        return f"{param}.player"
    if event_type in ("LivingHurtEvent", "LivingDeathEvent"):
        return f"{param}.entity"
    return f"{param}.player"

def _chain_has(call, names: List[str]) -> Optional[list]:
    """If `call` is followed by selector calls with exactly `names` (e.g. getTarget().getType().is(..)),
    return the argument list of the last one."""
    sels = [s for s in (call.selectors or []) if isinstance(s, jt.MethodInvocation)]
    if [s.member for s in sels[:len(names)]] == names and len(sels) >= len(names):
        return list(sels[len(names) - 1].arguments or [])
    return None

def _int_arg_text(node) -> Optional[str]:
    """Digits of an integer literal argument (no sign / suffix), else None."""
    if isinstance(node, jt.Literal) and not node.prefix_operators and str(node.value).isdigit():
        return str(node.value)
    return None

def _stack_calls(calls, member: str):
    """Calls `stack.member(..)`, `itemStack.member(..)`, `heldStack.member(..)` or `getItemStack().member(..)`."""
    for c in calls:
        if c.member == member and c.qualifier in ('stack', 'itemStack', 'heldStack'):
            yield c
    for c in calls:
        if c.member == 'getItemStack' and not c.arguments:
            sels = [s for s in (c.selectors or []) if isinstance(s, jt.MethodInvocation)]
            if sels and sels[0].member == member:
                yield sels[0]

def _registered_item_name(java_code_full, const_name: str) -> Optional[str]:
    for f in as_unit(java_code_full).fields():
        for d in f.declarators:
            if d.name == const_name and d.initializer is not None:
                for call in iter_calls(d.initializer):
                    if call.member == 'register' and call.arguments:
                        s = string_value(call.arguments[0])
                        if s:
                            return s
    return None

def _upper_const_segment(text: str) -> Optional[str]:
    """First `.CONSTANT` segment (upper-case identifier of 2+ chars) in a dotted expression text."""
    for seg in text.split('.')[1:]:
        ident = ''
        for ch in seg:
            if ch.isalnum() or ch == '_':
                ident += ch
            else:
                break
        if len(ident) >= 2 and ident[0].isupper() and ident == ident.upper() \
                and all(c.isupper() or c.isdigit() or c == '_' for c in ident):
            return ident
    return None

def _translate_handler_body(java_body, event_type: str, param: str, java_code_full, namespace: str, safe_name: str) -> list:

    lines = []
    player = _get_player_var(event_type, param)

    ast_lines = JavaAST.translate_java_body_to_js(java_body, event_type, param, namespace, safe_name)
    if ast_lines:
        lines.extend(ast_lines)
        return lines

    stmts = (java_body if isinstance(java_body, list) else parse_statements(java_body)) or []
    calls = list(iter_calls(stmts))
    members = {c.member for c in calls}

    def first(member, qualifier=None):
        return next((c for c in calls if c.member == member and (qualifier is None or c.qualifier == qualifier)), None)

    needs_inv = bool(members & {'shrink', 'addItem', 'getItemStack'})
    needs_block = bool(members & {'setBlock', 'getBlockState', 'getBlockPos', 'getHitVec'})

    if needs_inv:
        lines.append(f'    const inv = {player}.getComponent("minecraft:inventory").container;')
        lines.append(f'    const heldSlot = {player}.selectedSlotIndex;')
        lines.append(f'    let heldItem = inv.getItem(heldSlot);')

    if event_type == "PlayerInteractEvent.RightClickBlock" and needs_block:
        lines.append(f'    const block = {param}.block;')

    if event_type == "PlayerInteractEvent.EntityInteract":
        lines.append(f'    const target = {param}.target;')

    if members & {'isCrouching', 'isSneaking'}:
        lines.append(f'    if (!{player}.isSneaking) return;')

    null_checks = []
    for op in iter_nodes(stmts, jt.BinaryOperation):
        if op.operator == '!=' and isinstance(op.operandr, jt.Literal) and op.operandr.value == 'null' \
                and isinstance(op.operandl, jt.MemberReference) and not op.operandl.qualifier:
            null_checks.append(op.operandl.member)
    entity_null = any(c in ("entityInteractEvent", param, "entity", "player") for c in null_checks)
    get_entity_not_null = any(
        op.operator == '!=' and isinstance(op.operandr, jt.Literal) and op.operandr.value == 'null'
        and isinstance(op.operandl, jt.MethodInvocation) and op.operandl.member == 'getEntity' and not op.operandl.arguments
        for op in iter_nodes(stmts, jt.BinaryOperation))
    if not entity_null and get_entity_not_null:
        lines.append(f'    if (!{player}) return;')

    for c in calls:
        if c.member == 'getTarget' and not c.arguments:
            args = _chain_has(c, ['getType', 'is'])
            ref_text = dotted(args[0]) if args and len(args) == 1 else None
            if ref_text:
                ref = ref_text.split(".")[-1]
                tag_path = _extract_tag_path(java_code_full, ref)
                entity_hint = sanitize_identifier(tag_path.split("_for_")[0] if tag_path and "_for_" in tag_path else (tag_path or ref))
                lines.append(f'    if (!target || !target.typeId.includes("{entity_hint}")) return;')
                break

    item_is_checks = []
    for c in calls:
        if c.member == 'is' and c.qualifier in ('stack', 'itemStack') and len(c.arguments or []) == 1:
            a = c.arguments[0]
            if isinstance(a, jt.MemberReference) and not a.qualifier and not a.selectors:
                item_is_checks.append(a.member)
    for c in calls:
        if c.member == 'getItemStack' and not c.arguments:
            args = _chain_has(c, ['is'])
            if args and len(args) == 1 and isinstance(args[0], jt.MemberReference) and not args[0].qualifier:
                item_is_checks.append(args[0].member)
    for tag_ref in item_is_checks:
        lines.append(f'    if (!heldItem || heldItem.typeId !== "{namespace}:{sanitize_identifier(tag_ref)}") return;')

    if not item_is_checks:
        for c in calls:
            if c.member == 'is' and c.qualifier in ('stack', 'itemStack', 'heldStack') and len(c.arguments or []) == 1:
                ref_text = dotted(c.arguments[0])
                if ref_text:
                    lines.append(f'    if (!heldItem || heldItem.typeId !== "{namespace}:{sanitize_identifier(ref_text.split(".")[-1])}") return;')
                    break

    repair_call = 'repairState' in members
    if repair_call:
        lines.append(f'    const repairedId = repairBlockId(block.typeId);')
        lines.append(f'    if (!repairedId) return;')

    hb = first('hurtAndBreak')
    if hb is not None and hb.arguments and _int_arg_text(hb.arguments[0]):
        dmg = int(_int_arg_text(hb.arguments[0]))
        lines.append(f'    const dur = heldItem?.getComponent("minecraft:durability");')
        lines.append(f'    if (dur) {{ dur.damage = Math.min(dur.damage + {dmg}, dur.maxDurability); inv.setItem(heldSlot, heldItem); }}')

    if first('setBlock', 'level') is not None:
        if repair_call:
            lines.append(f'    block.setPermutation(BlockPermutation.resolve(repairedId));')
        else:
            lines.append(f'    block.setPermutation(block.permutation);')

    for c in calls:
        if c.member == 'playSound' and len(c.arguments or []) >= 5:
            snd = c.arguments[4]
            if isinstance(snd, jt.MemberReference) and snd.qualifier == 'SoundEvents':
                bedrock_sound = _SOUND_NAME_MAP.get(snd.member, "random.pop")
                lines.append(f'    {player}.dimension.playSound("{bedrock_sound}", {player}.location);')
                break

    if 'swing' in members:
        lines.append(f'    {player}.playAnimation("animation.player.attack.rotations");')

    instabuild = 'isCreative' in members or any(
        getattr(sel, 'member', None) == 'instabuild'
        for c in calls if c.member == 'getAbilities' for sel in (c.selectors or []))
    shrink = next((c for c in _stack_calls(calls, 'shrink') if len(c.arguments or []) == 1 and _int_arg_text(c.arguments[0])), None)
    if shrink is not None:
        amt = int(_int_arg_text(shrink.arguments[0]))
        if instabuild:
            lines.append(f'    if ({player}.getGameMode() !== GameMode.creative) {{')
            lines.append(f'        if (heldItem) {{ heldItem.amount = Math.max(0, heldItem.amount - {amt}); inv.setItem(heldSlot, heldItem.amount <= 0 ? undefined : heldItem); }}')
            lines.append(f'    }}')
        else:
            lines.append(f'    if (heldItem) {{ heldItem.amount = Math.max(0, heldItem.amount - {amt}); inv.setItem(heldSlot, heldItem.amount <= 0 ? undefined : heldItem); }}')

    add_item = first('addItem')
    if add_item is not None and add_item.arguments:
        arg = expr_text(add_item.arguments[0]).strip()
        const_name = _upper_const_segment(arg)
        if const_name:
            item_name = _registered_item_name(java_code_full, const_name) or sanitize_identifier(const_name)
        else:
            item_name = sanitize_identifier(arg.split(".")[0].strip())
        lines.append(f'    inv.addItem(new ItemStack("{namespace}:{item_name}"));')

    c = first('receiveEnergy', 'energy')
    if c is not None and len(c.arguments or []) == 1 and _int_arg_text(c.arguments[0]):
        lines.append(f'    receiveEnergy({player}, {_int_arg_text(c.arguments[0])});')
    c = first('extractEnergy', 'energy')
    if c is not None and len(c.arguments or []) == 1 and _int_arg_text(c.arguments[0]):
        lines.append(f'    extractEnergy({player}, {_int_arg_text(c.arguments[0])});')
    c = first('fill', 'fluid')
    if c is not None and len(c.arguments or []) == 2 and _int_arg_text(c.arguments[1]) \
            and isinstance(c.arguments[0], jt.MemberReference) and not c.arguments[0].qualifier:
        lines.append(f'    fill({player}, {{ type: "{namespace}:{c.arguments[0].member}", amount: {_int_arg_text(c.arguments[1])} }});')
    c = first('drain', 'fluid')
    if c is not None and len(c.arguments or []) == 1 and _int_arg_text(c.arguments[0]):
        lines.append(f'    drain({player}, {_int_arg_text(c.arguments[0])});')

    return lines

class JavaAST:
    """Query facade over a cached JavaUnit (javalang only - no regex fall-back)."""

    def __init__(self, source: str):
        self._src = source if isinstance(source, str) else ''
        self._unit = as_unit(source if not isinstance(source, str) else (source or ''))
        self._tree = self._unit.tree
        self._parsed = True

    def _parse(self):
        return None

    @property
    def error(self) -> Optional[str]:
        return self._unit.error

    def _classes(self):
        return self._unit.types()

    def primary_class_name(self) -> Optional[str]:
        t = self._unit.primary_type()
        return t.name if t is not None else None

    def package_name(self) -> Optional[str]:
        return self._unit.package_name()

    def imports(self) -> List[str]:
        return self._unit.import_paths()

    def annotation_value(self, annotation_name: str) -> Optional[str]:
        for node in self._unit.annotations():
            if node.name == annotation_name:
                args = annotation_args(node)
                val = args.get('value')
                if val is None:
                    return None
                s = string_value(val)
                return s if s is not None else expr_text(val)
        return None

    def field_string_values(self, field_names: Set[str]) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for node in self._unit.fields():
            for decl in node.declarators or []:
                if decl.name in field_names and decl.initializer is not None:
                    s = string_value(decl.initializer)
                    if s is not None:
                        out[decl.name] = s
        return out

    def get_class_declarations(self) -> List:
        return self._unit.classes()

    def superclass_name(self, cls_name: Optional[str] = None) -> Optional[str]:
        for node in self._unit.classes():
            if cls_name and node.name != cls_name:
                continue
            if node.extends is not None:
                return simple_type_name(node.extends)
        return None

    def implemented_interfaces(self, cls_name: Optional[str] = None) -> List[str]:
        for node in self._unit.classes():
            if cls_name and node.name != cls_name:
                continue
            return [simple_type_name(i) for i in (node.implements or [])]
        return []

    def all_class_extends(self) -> List[Tuple[str, str]]:
        return [(c.name, simple_type_name(c.extends)) for c in self._unit.classes() if c.extends is not None]

    def method_names(self) -> Set[str]:
        return {m.name for m in self._unit.methods()}

    def method_body_source(self, method_name: str) -> Optional[str]:
        return self._unit.method_body_text(method_name)

    def all_string_literals(self) -> List[str]:
        out = []
        for node in self._unit.walk(jt.Literal):
            s = string_value(node)
            if s is not None:
                out.append(s)
        return out

    def object_creations_of(self, class_name: str) -> List:
        return [n for n in self._unit.walk(jt.ClassCreator) if simple_type_name(n.type) == class_name]

    def all_object_creation_types(self) -> List[str]:
        return [simple_type_name(n.type) for n in self._unit.walk(jt.ClassCreator) if n.type is not None]

    def invocations_of(self, method_name: str) -> List:
        return [n for n in self._unit.walk(jt.MethodInvocation) if n.member == method_name]

    def instanceof_types(self) -> Set[str]:
        out: Set[str] = set()
        for node in self._unit.walk(jt.BinaryOperation):
            if node.operator == 'instanceof' and isinstance(node.operandr, (jt.ReferenceType, jt.BasicType)):
                out.add(simple_type_name(node.operandr))
        return out

    def class_extends(self, target_name: str, cls_name: Optional[str] = None) -> bool:
        return any(parent == target_name and (cls_name is None or child == cls_name) for child, parent in self.all_class_extends())

    @staticmethod
    def strip_generics(name: str) -> str:
        return strip_generics_name(name)

    @staticmethod
    def first_string_arg(invocation_node) -> Optional[str]:
        for arg in getattr(invocation_node, 'arguments', None) or []:
            s = string_value(arg)
            if s is not None:
                return s
        return None

    @staticmethod
    def translate_java_body_to_js(java_body, event_type: str, param: str, namespace: str, safe_name: str) -> list:
        if not java_body:
            return []
        stmts = java_body if isinstance(java_body, list) else parse_statements(java_body)
        if stmts is not None:
            player = _get_player_var(event_type, param)
            result_lines = []
            table = JavaSymbolTable()
            for stmt in stmts:
                result_lines.extend(translate_statement(stmt, player, namespace, table))
            return result_lines
        fallback = [f'// Fallback translation for {safe_name}']
        for raw_line in (java_body.splitlines() if isinstance(java_body, str) else []):
            if raw_line.strip():
                fallback.append(f'// {raw_line.rstrip()}')
        return fallback

class JavaSymbolTable:
    JAVA_TYPE_TO_BEDROCK = {
        'Player': 'Player', 'ServerPlayer': 'Player', 'LocalPlayer': 'Player',
        'Entity': 'Entity', 'LivingEntity': 'Entity', 'Mob': 'Entity', 'Monster': 'Entity',
        'ItemStack': 'ItemStack', 'Item': 'ItemTypeStr', 'BlockPos': 'Vector3', 'Vec3': 'Vector3',
        'Vec3i': 'Vector3', 'Vector3f': 'Vector3', 'BlockState': 'BlockPermutation',
        'Level': 'Dimension', 'ServerLevel': 'Dimension', 'World': 'Dimension',
        'Container': 'Container', 'Inventory': 'Container', 'SimpleContainer': 'Container',
        'CompoundTag': 'DynamicProperties', 'CompoundNBT': 'DynamicProperties',
        'ListTag': 'DynamicArray', 'ResourceLocation': 'string',
        'int': 'number', 'float': 'number', 'double': 'number', 'long': 'number', 'short': 'number', 'byte': 'number',
        'boolean': 'boolean', 'String': 'string', 'void': 'void', 'Object': 'any',
    }

    TYPE_METHOD_MAP = {
        'Player': {
            'sendMessage': '{0}.sendMessage({1})', 'getHealth': '{0}.getComponent("minecraft:health").currentValue',
            'setHealth': '{0}.getComponent("minecraft:health").setCurrentValue({1})', 'getInventory': '{0}.getComponent("minecraft:inventory").container',
            'getPosition': '{0}.location', 'setPosition': '{0}.teleport({1})', 'addExperiencePoints': '{0}.addExperience({1})',
        },
        'Entity': {
            'getHealth': '{0}.getComponent("minecraft:health").currentValue', 'setHealth': '{0}.getComponent("minecraft:health").setCurrentValue({1})',
            'getPosition': '{0}.location', 'setPosition': '{0}.teleport({1})', 'getVelocity': '{0}.getVelocity()', 'setVelocity': '{0}.applyImpulse({1})',
            'kill': '{0}.kill()', 'remove': '{0}.remove()', 'addTag': '{0}.addTag({1})', 'removeTag': '{0}.removeTag({1})', 'hasTag': '{0}.hasTag({1})',
        },
        'ItemStack': {
            'getCount': '{0}.amount', 'setCount': '{0}.amount = {1}', 'grow': '{0}.amount += {1}', 'shrink': '{0}.amount -= {1}', 'isEmpty': '({0}.amount <= 0)',
        },
        'Dimension': {
            'setBlockState': '{0}.getBlock({1}).setPermutation({2})', 'getBlockState': '{0}.getBlock({1}).permutation', 'addParticle': '{0}.spawnParticle({1}, {2})',
            'playSound': '{0}.playSound({1}, {2})',
        },
        'DynamicProperties': {
            'getInt': '({0}.getDynamicProperty({1}) ?? 0)', 'putInt': '{0}.setDynamicProperty({1}, {2})',
            'getString': '({0}.getDynamicProperty({1}) ?? "")', 'putString': '{0}.setDynamicProperty({1}, {2})',
            'getBoolean': '({0}.getDynamicProperty({1}) ?? false)', 'putBoolean': '{0}.setDynamicProperty({1}, {2})',
        },
        'Vector3': {
            'add': '{{ x: {0}.x + {1}.x, y: {0}.y + {1}.y, z: {0}.z + {1}.z }}', 'subtract': '{{ x: {0}.x - {1}.x, y: {0}.y - {1}.y, z: {0}.z - {1}.z }}',
            'scale': '{{ x: {0}.x * {1}, y: {0}.y * {1}, z: {0}.z * {1} }}', 'length': 'Math.sqrt({0}.x**2 + {0}.y**2 + {0}.z**2)',
        },
    }

    _CAP_ENERGY = {'receiveEnergy', 'extractEnergy', 'getEnergyStored', 'getMaxEnergyStored', 'canReceive', 'canExtract'}
    _CAP_FLUID = {'fill', 'drain', 'getFluidAmount', 'getTankCapacity', 'getFluidInTank', 'getTanks', 'isFluidValid'}
    _CAP_ITEM = {'insertItem', 'extractItem', 'getStackInSlot', 'getSlots', 'isItemValid', 'getSlotLimit'}
    _CAP_ITEMSTACK = {'getCount', 'setCount', 'grow', 'shrink', 'isEmpty'}

    def __init__(self):
        self.classes: Dict[str, Dict] = {}
        self.variables: Dict[str, str] = {}
        self._qualifier_type_cache: Dict[str, str] = {}
        self.method_return_types: Dict[str, str] = {}
        self._method_to_capability: Dict[str, str] = {}

    def register_class(self, class_name: str, superclass: Optional[str] = None, interfaces: List[str] = None):
        self.classes.setdefault(class_name, {'superclass': superclass, 'interfaces': interfaces or [], 'methods': {}, 'fields': {}})

    def register_method(self, class_name: str, method_name: str, return_type: str, params: Dict[str, str]):
        self.register_class(class_name)
        self.classes[class_name]['methods'][method_name] = {'return': return_type, 'params': params}
        self.method_return_types[f'{class_name}.{method_name}'] = return_type

    def register_field(self, class_name: str, field_name: str, field_type: str):
        self.register_class(class_name)
        self.classes[class_name]['fields'][field_name] = field_type

    def set_variable_type(self, var_name: str, var_type: str):
        if not var_name:
            return
        self.variables[var_name] = var_type
        resolved = self._resolve_bedrock_type(var_type)
        if resolved:
            self._qualifier_type_cache[var_name] = resolved

    def get_variable_type(self, var_name: str) -> Optional[str]:
        return self.variables.get(var_name)

    def _resolve_bedrock_type(self, java_type: str) -> Optional[str]:
        return self.JAVA_TYPE_TO_BEDROCK.get(strip_generics_name(java_type))

    def get_bedrock_type_for_var(self, var_name: str) -> Optional[str]:
        if not var_name:
            return None
        if var_name in self._qualifier_type_cache:
            return self._qualifier_type_cache[var_name]
        if var_name in self.variables:
            return self._resolve_bedrock_type(self.variables[var_name])
        low = var_name.lower()
        if low in {'player', 'p', 'serverplayer', 'localplayer'}:
            return 'Player'
        if low in {'entity', 'mob', 'e', 'target', 'attacker', 'victim'}:
            return 'Entity'
        if low in {'stack', 'itemstack', 'item', 'helditem', 'mainhand', 'offhand'}:
            return 'ItemStack'
        if low in {'level', 'world', 'dimension', 'serverlevel', 'dim'}:
            return 'Dimension'
        if low in {'nbt', 'tag', 'compound', 'data', 'persistentdata'}:
            return 'DynamicProperties'
        if low in {'pos', 'blockpos', 'position', 'origin', 'loc', 'location'}:
            return 'Vector3'
        if low in {'inventory', 'container', 'inv', 'chest', 'slots'}:
            return 'Container'
        return None

    def resolve_method_call(self, qualifier: str, method: str, args: List[str]) -> Optional[str]:
        btype = self.get_bedrock_type_for_var(qualifier)
        if not btype:
            return None
        tmpl = self.TYPE_METHOD_MAP.get(btype, {}).get(method)
        if not tmpl:
            return None
        result = tmpl.replace('{0}', qualifier)
        for i, arg in enumerate(args):
            result = result.replace(f'{{{i + 1}}}', arg)
        return result

    def method_belongs_to_capability(self, method_name: str) -> Optional[str]:
        if method_name in self._CAP_ENERGY:
            return 'energy'
        if method_name in self._CAP_FLUID:
            return 'fluid'
        if method_name in self._CAP_ITEM:
            return 'item_handler'
        if method_name in self._CAP_ITEMSTACK:
            return 'itemstack'
        return None

    def scan_java_file(self, java_code: str):
        unit = get_unit(java_code)
        for node in unit.classes():
            super_name = simple_type_name(node.extends) if node.extends is not None else None
            interfaces = [simple_type_name(i) for i in (node.implements or [])]
            self.register_class(node.name, super_name, interfaces)
            for field in node.fields:
                ftype = type_name(field.type)
                for decl in field.declarators:
                    self.register_field(node.name, decl.name, ftype)
                    self.set_variable_type(decl.name, ftype)
            for method in node.methods:
                ret = type_name(method.return_type) if method.return_type else 'void'
                params = {}
                for prm in method.parameters or []:
                    ptype = type_name(prm.type)
                    params[prm.name] = ptype
                    self.set_variable_type(prm.name, ptype)
                self.register_method(node.name, method.name, ret, params)
        for node in unit.walk(jt.LocalVariableDeclaration):
            ltype = type_name(node.type)
            for decl in node.declarators:
                self.set_variable_type(decl.name, ltype)

def translate_method_invocation(invocation: object, player: str, namespace: str, symbol_table: JavaSymbolTable) -> Optional[str]:
    member = getattr(invocation, 'member', '')
    qualifier = getattr(invocation, 'qualifier', None)
    if isinstance(qualifier, list):
        qualifier = qualifier[0] if qualifier else None
    args = getattr(invocation, 'arguments', []) or []
    arg_strs = [translate_expression(a) for a in args]
    arg_strs = [a for a in arg_strs if a is not None]
    if qualifier and isinstance(qualifier, str):
        resolved = symbol_table.resolve_method_call(qualifier, member, arg_strs)
        if resolved:
            return resolved
    if member in {'receiveEnergy', 'extractEnergy'} and arg_strs:
        return f'{member}({player}, {arg_strs[0]});'
    nbt_result = NBTTranslator.translate_nbt_call(member, args, namespace, player)
    if nbt_result:
        return nbt_result
    cap_type = symbol_table.method_belongs_to_capability(member)
    if cap_type == 'energy' and member == 'getEnergyStored':
        return f'getEnergyStored({player})'
    if cap_type == 'fluid' and member in {'fill', 'drain'}:
        return f'{member}({player}, {", ".join(arg_strs)})'
    bedrock_call = JavaToBedrockMethodMap.translate_method_call(member, args, qualifier)
    if bedrock_call:
        return bedrock_call
    return None

def _extract_method_body(source: str, method_name) -> Optional[str]:
    """Body text (between the braces) of a named method; `method_name` may be a list of names."""
    if not source or not method_name:
        return None
    return get_unit(source).method_body_text(method_name)
