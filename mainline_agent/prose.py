"""Conservative checks for explicit old defaults in prose and Markdown tables."""
import ast
import re
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class DefaultFact:
    key: str
    identifier: str
    owner: str
    value: object
    line: int


def default_facts(text):
    tree = ast.parse(text)
    facts = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.decorator_list:
            args = node.args
            positional = list(args.posonlyargs) + list(args.args)
            pairs = list(zip(positional[-len(args.defaults):], args.defaults)) if args.defaults else []
            pairs += [(arg, value) for arg, value in zip(args.kwonlyargs, args.kw_defaults) if value is not None]
            for arg, value in pairs:
                if isinstance(value, ast.Constant) and isinstance(value.value, (str, int, float, bool, type(None))):
                    key = node.name + '.' + arg.arg
                    facts[key] = DefaultFact(key, arg.arg, node.name, value.value, node.lineno)
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and isinstance(value, ast.Constant):
                    facts[target.id] = DefaultFact(target.id, target.id, '', value.value, node.lineno)
    # argparse default declarations are linked by their exact option spelling.
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'add_argument':
            defaults = [k.value for k in node.keywords if k.arg == 'default' and isinstance(k.value, ast.Constant)]
            for arg in node.args:
                if defaults and isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value.startswith('--'):
                    key = 'flag:' + arg.value
                    facts[key] = DefaultFact(key, arg.value, '', defaults[0].value, node.lineno)
    return facts


def passages(text):
    """Yield exact prose sentences or table rows with original offsets."""
    fenced = False
    header = ''
    paragraph = ''
    paragraph_start = 0
    offset = 0
    for line in text.splitlines(keepends=True) + ['']:
        stripped = line.strip()
        boundary = not stripped or stripped.startswith(('#', '```', '~~~')) or '|' in stripped
        if boundary and paragraph:
            for match in re.finditer(r'.+?(?:[.!?](?=\s|$)|$)', paragraph, re.S):
                if match.group().strip():
                    yield paragraph_start + match.start(), match.group(), ''
            paragraph = ''
        if stripped.startswith(('```', '~~~')):
            fenced = not fenced
        elif not fenced and stripped and not stripped.startswith('#'):
            if '|' in stripped:
                if 'default' in stripped.lower():
                    header = stripped.lower()
                yield offset, line.rstrip('\r\n'), header
            else:
                header = ''
                if not paragraph:
                    paragraph_start = offset
                paragraph += line
        offset += len(line)


def span(text, quote, start=0, first_line=1):
    offset = text.index(quote, start)
    prefix = text[:offset]
    end = offset + len(quote)
    return {'line': first_line + prefix.count('\n'),
            'end_line': first_line + text[:end].count('\n'),
            'column': len(prefix.rsplit('\n', 1)[-1]) + 1,
            'end_column': len(text[:end].rsplit('\n', 1)[-1]) + 1,
            'quote': quote}


def default_drift(path, old, new, documents, base, head, initial=False):
    if not path.endswith('.py'):
        return []
    try:
        before, after = default_facts(old), default_facts(new)
    except SyntaxError:
        return []
    findings = []
    comparisons = [(key, before[key], after[key]) for key in sorted(before.keys() & after.keys())]
    if initial:
        comparisons = []
        for key, fact in after.items():
            for doc in documents:
                for _, passage, header in passages(doc['text']):
                    claim = re.search(r"(?:defaults?(?:\s+value)?\s+(?:to|is|are)|default\s*[:=])\s*(`[^`]+`|[^\s,;]+)", passage, re.I)
                    token = claim.group(1).strip('`').rstrip('.') if claim else None
                    if 'default' in header and '|' in passage:
                        headings = [part.strip() for part in header.strip('|').split('|')]
                        cells = [part.strip() for part in passage.strip().strip('|').split('|')]
                        if 'default' in headings and len(cells) > headings.index('default'):
                            token = cells[headings.index('default')].strip('`')
                    if not token:
                        continue
                    try:
                        value = ast.literal_eval(token)
                    except (ValueError, SyntaxError):
                        value = {'true': True, 'false': False, 'null': None}.get(token.lower(), token)
                    if isinstance(value, (str, int, float, bool, type(None))):
                        comparisons.append((key, replace(fact, value=value), fact))
    seen = set()
    for key, prior, current in comparisons:
        if type(prior.value) is type(current.value) and prior.value == current.value:
            continue
        if not isinstance(prior.value, (str, int, float, bool, type(None))):
            continue
        token = str(prior.value)
        if not token:
            continue
        value_pattern = re.compile(r'(?<![\w.])' + re.escape(token) + r'(?!\w|\.\d)', re.I)
        identifier_pattern = re.compile(r'(?<![\w-])' + re.escape(prior.identifier) + r'(?![\w-])')
        for doc in documents:
            text = doc['text']
            for offset, passage, header in passages(text):
                if not identifier_pattern.search(passage):
                    continue
                # An unqualified common parameter name needs its function name as context.
                context = text[max(0, offset - 400):offset + len(passage)]
                if prior.owner and not re.search(r'\b' + re.escape(prior.owner) + r'\b', context):
                    continue
                if not re.search(r'\bdefaults?(?:\s+value)?\s+(?:to|is|are)\b|\bby\s+default\b|\bdefault(?:\s+value)?\s*[:=]', passage, re.I) and 'default' not in header:
                    continue
                if re.search(r'\b(previously|formerly|used to|historical|deprecated)\b', passage, re.I):
                    continue
                if not value_pattern.search(passage):
                    continue
                # Conflicting old/new mentions can describe migration. Leave those to semantic review.
                if re.search(r'(?<![\w.])' + re.escape(str(current.value)) + r'(?!\w|\.\d)', passage, re.I):
                    continue
                identity = (key, doc['path'], offset)
                if identity in seen:
                    continue
                seen.add(identity)
                findings.append({'kind': 'documentation_drift', 'confidence': 'possible_static',
                    'summary': 'Documented default for %s still mentions %r after the declared default changed to %r.' % (key, prior.value, current.value),
                    'evidence': [dict(path=doc['path'], sha=head, type='doc_head', **span(text, passage, offset)),
                                 {'path': path, 'sha': base, 'line': prior.line, 'quote': old.splitlines()[prior.line - 1]},
                                 {'path': path, 'sha': head, 'line': current.line, 'quote': new.splitlines()[current.line - 1]}],
                    'limitation': 'Explicit identifier/default/value match. Maintainer review establishes semantic scope.'})
    if initial:
        for finding in findings:
            finding['summary'] = finding['summary'].replace('still mentions', 'mentions').replace('after the declared default changed to', 'while the current declared default is')
            finding['evidence'] = [item for item in finding['evidence'] if item.get('sha') != base]
    return findings
