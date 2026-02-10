#!/usr/bin/env python3
"""
FM_CP — FileMaker Compose / Parse
Bidirectional converter between plain text and FileMaker XML clipboard snippets.

Usage:
    fm-cp -c          # Auto-detect clipboard, convert either direction
    fm-cp script.txt  # Compose plain text file → FM clipboard
    fm-cp dump        # Raw FM XML clipboard dump
"""

from fm_cp import __version__

# ============================================================
#  CHANGELOG
# ============================================================
# 0.3.2  2026-02-10  Cleanup for public release: removed fm_clip.py
#                     fallback, single version source (__init__.py),
#                     added LICENSE, project URLs, README framing.
# 0.3.1  2026-02-09  Multi-line comment round-trip: decompiler
#                     prefixes every line with #, composer merges
#                     consecutive # lines into single FM comment
#                     step with &#10; delimiters.
# 0.3.0  2026-02-09  Multi-line calc parser (balanced paren/bracket
#                     accumulation), // disabled step support in
#                     compose, default routing: XML→decompile,
#                     text→compose. -text flag removed.
# 0.2.0  2026-02-09  Unified CLI with auto-detect, -o file output,
#                     platform-agnostic file I/O, 8 new step types
#                     (Set Field, Insert Text, New Window, Adjust
#                     Window, Refresh Window, Halt Script, Configure
#                     LLM Template, LLM Request). 31 total step types.
# 0.1.0  2026-02-09  Initial release. 23 step types, compose +
#                     decompile, FM clipboard integration (PyObjC),
#                     structural validation, macOS pasteboard support.
# ============================================================

import re
import sys

# ============================================================
#  STEP DEFINITIONS
# ============================================================

# Maps step type keywords to their FM step IDs and parser config
STEP_DEFS = {
    'comment':              {'id': 89,  'name': '# (comment)'},
    'set_error_capture':    {'id': 86,  'name': 'Set Error Capture'},
    'allow_user_abort':     {'id': 85,  'name': 'Allow User Abort'},
    'set_variable':         {'id': 141, 'name': 'Set Variable'},
    'set_field_by_name':    {'id': 147, 'name': 'Set Field By Name'},
    'set_field':            {'id': 125, 'name': 'Set Field'},
    'if':                   {'id': 68,  'name': 'If'},
    'else_if':              {'id': 125, 'name': 'Else If'},
    'else':                 {'id': 69,  'name': 'Else'},
    'end_if':               {'id': 70,  'name': 'End If'},
    'loop':                 {'id': 71,  'name': 'Loop'},
    'exit_loop_if':         {'id': 72,  'name': 'Exit Loop If'},
    'end_loop':             {'id': 73,  'name': 'End Loop'},
    'show_custom_dialog':   {'id': 87,  'name': 'Show Custom Dialog'},
    'exit_script':          {'id': 103, 'name': 'Exit Script'},
    'commit_records':       {'id': 75,  'name': 'Commit Records/Requests'},
    'insert_from_url':      {'id': 160, 'name': 'Insert from URL'},
    'perform_script':       {'id': 1,   'name': 'Perform Script'},
    'go_to_layout':         {'id': 6,   'name': 'Go to Layout'},
    'go_to_record':         {'id': 16,  'name': 'Go to Record/Request/Page'},
    'new_record':           {'id': 7,   'name': 'New Record/Request'},
    'enter_find_mode':      {'id': 22,  'name': 'Enter Find Mode'},
    'perform_find':         {'id': 28,  'name': 'Perform Find'},
    'sort_records':         {'id': 39,  'name': 'Sort Records'},
}


# ============================================================
#  STAGE 1: PARSER
# ============================================================

class ParsedStep:
    """Represents a single parsed script step."""
    def __init__(self, step_type, params=None, line_num=0, raw_text='', enabled=True):
        self.step_type = step_type
        self.params = params or {}
        self.line_num = line_num
        self.raw_text = raw_text
        self.enabled = enabled

    def __repr__(self):
        return f"<Step L{self.line_num}: {self.step_type} {self.params}>"


def parse_line(line, line_num):
    """Parse a single line of plain text into a ParsedStep or error string."""
    stripped = line.strip()

    # --- Skip blank lines ---
    if not stripped:
        return None

    # --- Disabled step: // prefix ---
    if stripped.startswith('// '):
        inner = stripped[3:].strip()
        if not inner:
            return None
        result = parse_line(inner, line_num)
        if isinstance(result, ParsedStep):
            result.enabled = False
        return result

    # --- Comment: # text ---
    if stripped.startswith('#'):
        text = stripped[1:].strip()
        return ParsedStep('comment', {'text': text}, line_num, stripped)

    # --- Set Error Capture [ On/Off ] ---
    m = re.match(r'^Set Error Capture\s*\[\s*(On|Off)\s*\]$', stripped, re.IGNORECASE)
    if m:
        val = m.group(1).capitalize()
        return ParsedStep('set_error_capture', {'state': val}, line_num, stripped)

    # --- Allow User Abort [ On/Off ] ---
    m = re.match(r'^Allow User Abort\s*\[\s*(On|Off)\s*\]$', stripped, re.IGNORECASE)
    if m:
        val = m.group(1).capitalize()
        return ParsedStep('allow_user_abort', {'state': val}, line_num, stripped)

    # --- Set Variable [ $name ; Value: expression ] ---
    m = re.match(r'^Set Variable\s*\[\s*(\${1,2}[\w.]+)\s*;\s*Value:\s*(.+?)\s*\]$', stripped, re.IGNORECASE)
    if m:
        return ParsedStep('set_variable', {
            'name': m.group(1),
            'value': m.group(2)
        }, line_num, stripped)

    # --- Set Field By Name [ "Table::Field" ; expression ] ---
    m = re.match(r'^Set Field By Name\s*\[\s*(.+?)\s*;\s*(.+?)\s*\]$', stripped, re.IGNORECASE)
    if m:
        return ParsedStep('set_field_by_name', {
            'target': m.group(1),
            'value': m.group(2)
        }, line_num, stripped)

    # --- Set Field [ Table::Field ; expression ] ---
    m = re.match(r'^Set Field\s*\[\s*(.+?)\s*;\s*(.+?)\s*\]$', stripped, re.IGNORECASE)
    if m:
        return ParsedStep('set_field', {
            'field': m.group(1),
            'value': m.group(2)
        }, line_num, stripped)

    # --- If [ calculation ] ---
    m = re.match(r'^If\s*\[\s*(.+?)\s*\]$', stripped, re.IGNORECASE)
    if m:
        return ParsedStep('if', {'calc': m.group(1)}, line_num, stripped)

    # --- Else If [ calculation ] ---
    m = re.match(r'^Else If\s*\[\s*(.+?)\s*\]$', stripped, re.IGNORECASE)
    if m:
        return ParsedStep('else_if', {'calc': m.group(1)}, line_num, stripped)

    # --- Else ---
    if re.match(r'^Else$', stripped, re.IGNORECASE):
        return ParsedStep('else', {}, line_num, stripped)

    # --- End If ---
    if re.match(r'^End If$', stripped, re.IGNORECASE):
        return ParsedStep('end_if', {}, line_num, stripped)

    # --- Loop ---
    if re.match(r'^Loop$', stripped, re.IGNORECASE):
        return ParsedStep('loop', {}, line_num, stripped)

    # --- Exit Loop If [ calculation ] ---
    m = re.match(r'^Exit Loop If\s*\[\s*(.+?)\s*\]$', stripped, re.IGNORECASE)
    if m:
        return ParsedStep('exit_loop_if', {'calc': m.group(1)}, line_num, stripped)

    # --- End Loop ---
    if re.match(r'^End Loop$', stripped, re.IGNORECASE):
        return ParsedStep('end_loop', {}, line_num, stripped)

    # --- Show Custom Dialog [ "title" ; "message" ; "button1" ; "button2" ; "button3" ] ---
    m = re.match(r'^Show Custom Dialog\s*\[\s*(.+?)\s*\]$', stripped, re.IGNORECASE)
    if m:
        inner = m.group(1)
        # Split on ; but respect quoted strings and parentheses
        parts = _split_params(inner)
        params = {'title': parts[0].strip() if len(parts) > 0 else '""'}
        params['message'] = parts[1].strip() if len(parts) > 1 else '""'
        params['buttons'] = [p.strip() for p in parts[2:]] if len(parts) > 2 else ['"OK"']
        return ParsedStep('show_custom_dialog', params, line_num, stripped)

    # --- Exit Script [ result ] ---
    m = re.match(r'^Exit Script\s*\[\s*(.+?)\s*\]$', stripped, re.IGNORECASE)
    if m:
        return ParsedStep('exit_script', {'result': m.group(1)}, line_num, stripped)

    # --- Exit Script (no param) ---
    if re.match(r'^Exit Script$', stripped, re.IGNORECASE):
        return ParsedStep('exit_script', {'result': ''}, line_num, stripped)

    # --- Commit Records [ No dialog ] or Commit Records ---
    m = re.match(r'^Commit Records(?:/Requests)?\s*(?:\[\s*(.*?)\s*\])?$', stripped, re.IGNORECASE)
    if m:
        opts = m.group(1) or ''
        no_dialog = 'no dialog' in opts.lower() or 'skip' in opts.lower()
        return ParsedStep('commit_records', {'no_dialog': no_dialog}, line_num, stripped)

    # --- Perform Script [ "scriptname" ; parameter ] ---
    m = re.match(r'^Perform Script\s*\[\s*(.+?)\s*\]$', stripped, re.IGNORECASE)
    if m:
        parts = _split_params(m.group(1))
        params = {'script_name': parts[0].strip()}
        params['parameter'] = parts[1].strip() if len(parts) > 1 else ''
        return ParsedStep('perform_script', params, line_num, stripped)

    # --- Go to Layout [ "layoutname" ] ---
    m = re.match(r'^Go to Layout\s*\[\s*(.+?)\s*\]$', stripped, re.IGNORECASE)
    if m:
        return ParsedStep('go_to_layout', {'layout': m.group(1).strip()}, line_num, stripped)

    # --- Insert from URL [ options ] ---
    m = re.match(r'^Insert from URL\s*\[\s*(.+?)\s*\]$', stripped, re.IGNORECASE)
    if m:
        parts = _split_params(m.group(1))
        params = {}
        for p in parts:
            p = p.strip()
            if p.lower().startswith('target:'):
                params['target'] = p[7:].strip()
            elif p.lower().startswith('url:'):
                params['url'] = p[4:].strip()
            elif p.lower().startswith('curl:') or p.lower().startswith('curloptions:'):
                params['curl'] = p.split(':', 1)[1].strip()
            else:
                # First unlabeled = target, second = url
                if 'target' not in params:
                    params['target'] = p
                elif 'url' not in params:
                    params['url'] = p
        return ParsedStep('insert_from_url', params, line_num, stripped)

    # --- New Record ---
    if re.match(r'^New Record(/Request)?$', stripped, re.IGNORECASE):
        return ParsedStep('new_record', {}, line_num, stripped)

    # --- Enter Find Mode ---
    m = re.match(r'^Enter Find Mode\s*(?:\[\s*(.*?)\s*\])?$', stripped, re.IGNORECASE)
    if m:
        pause = m.group(1) or ''
        return ParsedStep('enter_find_mode', {'pause': 'pause' in pause.lower()}, line_num, stripped)

    # --- Perform Find ---
    if re.match(r'^Perform Find(?:\s*\[\s*\])?$', stripped, re.IGNORECASE):
        return ParsedStep('perform_find', {}, line_num, stripped)

    # --- Go to Record [ First/Last/Next/Previous ] ---
    m = re.match(r'^Go to Record(?:/Request/Page)?\s*\[\s*(First|Last|Next|Previous)\s*\]$', stripped, re.IGNORECASE)
    if m:
        return ParsedStep('go_to_record', {'direction': m.group(1).capitalize()}, line_num, stripped)

    # --- Sort Records ---
    m = re.match(r'^Sort Records\s*(?:\[\s*(.*?)\s*\])?$', stripped, re.IGNORECASE)
    if m:
        return ParsedStep('sort_records', {}, line_num, stripped)

    # --- Unrecognized ---
    return f"Line {line_num}: Unrecognized step: {stripped}"


def _split_params(s):
    """Split parameters on semicolons, respecting quotes and nested parens/brackets."""
    parts = []
    current = []
    depth_paren = 0
    depth_bracket = 0
    in_quote = False
    escape = False

    for ch in s:
        if escape:
            current.append(ch)
            escape = False
            continue
        if ch == '\\':
            escape = True
            current.append(ch)
            continue
        if ch == '"' and depth_paren == 0:
            in_quote = not in_quote
            current.append(ch)
            continue
        if in_quote:
            current.append(ch)
            continue
        if ch == '(':
            depth_paren += 1
        elif ch == ')':
            depth_paren -= 1
        elif ch == '[':
            depth_bracket += 1
        elif ch == ']':
            depth_bracket -= 1
        elif ch == ';' and depth_paren == 0 and depth_bracket == 0:
            parts.append(''.join(current))
            current = []
            continue
        current.append(ch)

    if current:
        parts.append(''.join(current))
    return parts


def _count_delimiters(text):
    """Count unbalanced (), [] in text, respecting quotes.
    Returns (paren_depth, bracket_depth)."""
    paren = 0
    bracket = 0
    in_quote = False
    escape = False
    for ch in text:
        if escape:
            escape = False
            continue
        if ch == '\\':
            escape = True
            continue
        if ch == '"':
            in_quote = not in_quote
            continue
        if in_quote:
            continue
        if ch == '(':
            paren += 1
        elif ch == ')':
            paren -= 1
        elif ch == '[':
            bracket += 1
        elif ch == ']':
            bracket -= 1
    return paren, bracket


def parse_text(text):
    """Parse multi-line plain text into a list of ParsedSteps.
    Accumulates continuation lines when delimiters are unbalanced.
    Returns (steps, errors) tuple."""
    steps = []
    errors = []
    lines = text.split('\n')

    accumulator = []    # lines being accumulated
    start_line = 0      # line number where accumulation started
    paren_depth = 0
    bracket_depth = 0

    def _flush():
        """Send accumulated logical line to parse_line."""
        nonlocal accumulator, start_line, paren_depth, bracket_depth
        if not accumulator:
            return
        # Join with newline, but parse_line regexes expect single line
        # Collapse to single line: join with space, normalize whitespace
        logical = ' '.join(l.strip() for l in accumulator if l.strip())
        result = parse_line(logical, start_line)
        if result is None:
            pass  # blank
        elif isinstance(result, str):
            errors.append(result)
        else:
            steps.append(result)
        accumulator = []
        paren_depth = 0
        bracket_depth = 0

    for i, line in enumerate(lines, 1):
        stripped = line.strip()

        # If we're NOT accumulating, handle normally
        if not accumulator:
            if not stripped:
                continue  # blank line

            # Start a new logical line
            start_line = i
            accumulator.append(line)
            p, b = _count_delimiters(stripped)
            paren_depth += p
            bracket_depth += b

            # If balanced, flush immediately
            if paren_depth <= 0 and bracket_depth <= 0:
                _flush()
        else:
            # We're accumulating — append continuation
            accumulator.append(line)
            p, b = _count_delimiters(stripped)
            paren_depth += p
            bracket_depth += b

            # Check if balanced now
            if paren_depth <= 0 and bracket_depth <= 0:
                _flush()

    # Flush anything remaining (unbalanced at EOF)
    if accumulator:
        _flush()

    # Merge consecutive comment steps into single multi-line comments
    steps = _merge_comments(steps)

    return steps, errors


def _merge_comments(steps):
    """Merge consecutive comment steps (same enabled state) into one."""
    if not steps:
        return steps
    merged = [steps[0]]
    for step in steps[1:]:
        prev = merged[-1]
        if (step.step_type == 'comment' and prev.step_type == 'comment'
                and step.enabled == prev.enabled):
            # Merge text with newline
            prev.params['text'] = prev.params.get('text', '') + '\n' + step.params.get('text', '')
        else:
            merged.append(step)
    return merged


# ============================================================
#  STAGE 2: STRUCTURAL VALIDATOR
# ============================================================

class ValidationResult:
    """Container for validation results."""
    def __init__(self):
        self.errors = []
        self.warnings = []

    @property
    def is_valid(self):
        return len(self.errors) == 0

    def add_error(self, line_num, message):
        self.errors.append(f"Line {line_num}: ERROR — {message}")

    def add_warning(self, line_num, message):
        self.warnings.append(f"Line {line_num}: WARN — {message}")

    def report(self):
        lines = []
        lines.append("=" * 60)
        lines.append("  FM_CP — Structural Validation")
        lines.append("=" * 60)

        if self.errors:
            lines.append("")
            for e in self.errors:
                lines.append(f"  ✗ {e}")

        if self.warnings:
            lines.append("")
            for w in self.warnings:
                lines.append(f"  ⚠ {w}")

        lines.append("")
        if self.is_valid:
            lines.append("  ✓ PASS — Structure is valid")
        else:
            lines.append(f"  ✗ FAIL — {len(self.errors)} error(s) found")

        lines.append("=" * 60)
        return '\n'.join(lines)


def validate_structure(steps):
    """Validate structural integrity of parsed steps.
    Returns a ValidationResult."""
    result = ValidationResult()
    block_stack = []  # (type, line_num) — tracks open blocks

    for step in steps:
        st = step.step_type
        ln = step.line_num

        # --- If opens a block ---
        if st == 'if':
            block_stack.append(('if', ln, False))  # (type, line, has_else)

        # --- Else If must be inside an If block ---
        elif st == 'else_if':
            if not block_stack or block_stack[-1][0] != 'if':
                result.add_error(ln, "Else If without matching If")
            elif block_stack[-1][2]:  # already has Else
                result.add_error(ln, "Else If after Else (must come before Else)")

        # --- Else must be inside an If block ---
        elif st == 'else':
            if not block_stack or block_stack[-1][0] != 'if':
                result.add_error(ln, "Else without matching If")
            elif block_stack[-1][2]:
                result.add_error(ln, "Duplicate Else — only one Else per If block")
            else:
                # Mark this If block as having an Else
                block_stack[-1] = ('if', block_stack[-1][1], True)

        # --- End If closes an If block ---
        elif st == 'end_if':
            if not block_stack:
                result.add_error(ln, "Orphan End If — no matching If")
            elif block_stack[-1][0] != 'if':
                result.add_error(ln, f"End If found but current open block is {block_stack[-1][0].title()} (opened line {block_stack[-1][1]})")
            else:
                block_stack.pop()

        # --- Loop opens a block ---
        elif st == 'loop':
            block_stack.append(('loop', ln, False))

        # --- Exit Loop If must be inside a Loop ---
        elif st == 'exit_loop_if':
            # Check if any ancestor is a loop
            in_loop = any(b[0] == 'loop' for b in block_stack)
            if not in_loop:
                result.add_error(ln, "Exit Loop If outside of any Loop")

        # --- End Loop closes a Loop block ---
        elif st == 'end_loop':
            if not block_stack:
                result.add_error(ln, "Orphan End Loop — no matching Loop")
            elif block_stack[-1][0] != 'loop':
                result.add_error(ln, f"End Loop found but current open block is {block_stack[-1][0].title()} (opened line {block_stack[-1][1]})")
            else:
                block_stack.pop()

    # --- Check for unclosed blocks ---
    for block_type, open_line, _ in block_stack:
        result.add_error(open_line, f"Unclosed {block_type.title()} — missing End {block_type.title()}")

    # --- Advisory: step count ---
    if len(steps) == 0:
        result.add_warning(0, "No steps found — is the input empty?")

    return result


# ============================================================
#  STAGE 3: XML GENERATOR
# ============================================================

def _cdata(value):
    """Wrap value in CDATA."""
    return f"<![CDATA[{value}]]>"


def _strip_outer_quotes(s):
    """Remove surrounding quotes if present."""
    s = s.strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        return s[1:-1]
    return s


def step_to_xml(step):
    """Convert a ParsedStep to FM XML string."""
    st = step.step_type
    p = step.params
    sid = STEP_DEFS.get(st, {}).get('id', 0)
    sname = STEP_DEFS.get(st, {}).get('name', '')

    if st == 'comment':
        text = p.get('text', '')
        # Encode special chars for XML
        text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        text = text.replace('\n', '&#10;').replace('"', '&quot;')
        return f'<Step enable="True" id="89" name="# (comment)"><Text>{text}</Text></Step>'

    elif st == 'set_error_capture':
        val = 'True' if p.get('state') == 'On' else 'False'
        return f'<Step enable="True" id="86" name="Set Error Capture"><Set state="{val}"></Set></Step>'

    elif st == 'allow_user_abort':
        val = 'True' if p.get('state') == 'On' else 'False'
        return f'<Step enable="True" id="85" name="Allow User Abort"><Set state="{val}"></Set></Step>'

    elif st == 'set_variable':
        name = p.get('name', '')
        value = p.get('value', '')
        return (f'<Step enable="True" id="141" name="Set Variable">'
                f'<Value><Calculation>{_cdata(value)}</Calculation></Value>'
                f'<Repetition><Calculation>{_cdata("1")}</Calculation></Repetition>'
                f'<Name>{name}</Name></Step>')

    elif st == 'set_field_by_name':
        target = p.get('target', '')
        value = p.get('value', '')
        return (f'<Step enable="True" id="147" name="Set Field By Name">'
                f'<Result><Calculation>{_cdata(value)}</Calculation></Result>'
                f'<TargetName><Calculation>{_cdata(target)}</Calculation></TargetName></Step>')

    elif st == 'if':
        calc = p.get('calc', '')
        return (f'<Step enable="True" id="68" name="If">'
                f'<Calculation>{_cdata(calc)}</Calculation></Step>')

    elif st == 'else_if':
        calc = p.get('calc', '')
        return (f'<Step enable="True" id="125" name="Else If">'
                f'<Calculation>{_cdata(calc)}</Calculation></Step>')

    elif st == 'else':
        return '<Step enable="True" id="69" name="Else"></Step>'

    elif st == 'end_if':
        return '<Step enable="True" id="70" name="End If"></Step>'

    elif st == 'loop':
        return '<Step enable="True" id="71" name="Loop"><FlushType value="Always"></FlushType></Step>'

    elif st == 'exit_loop_if':
        calc = p.get('calc', '')
        return (f'<Step enable="True" id="72" name="Exit Loop If">'
                f'<Calculation>{_cdata(calc)}</Calculation></Step>')

    elif st == 'end_loop':
        return '<Step enable="True" id="73" name="End Loop"></Step>'

    elif st == 'show_custom_dialog':
        title = p.get('title', '""')
        message = p.get('message', '""')
        buttons = p.get('buttons', ['"OK"'])

        xml = (f'<Step enable="True" id="87" name="Show Custom Dialog">'
               f'<Title><Calculation>{_cdata(title)}</Calculation></Title>'
               f'<Message><Calculation>{_cdata(message)}</Calculation></Message>'
               f'<Buttons>')

        # Ensure 3 buttons (FM requires all 3 elements)
        while len(buttons) < 3:
            buttons.append('')
        for btn in buttons[:3]:
            if btn:
                xml += f'<Button><Calculation>{_cdata(btn)}</Calculation></Button>'
            else:
                xml += '<Button></Button>'
        xml += '</Buttons></Step>'
        return xml

    elif st == 'exit_script':
        result_val = p.get('result', '')
        if result_val:
            return (f'<Step enable="True" id="103" name="Exit Script">'
                    f'<Calculation>{_cdata(result_val)}</Calculation></Step>')
        else:
            return '<Step enable="True" id="103" name="Exit Script"></Step>'

    elif st == 'commit_records':
        no_dialog = p.get('no_dialog', False)
        nd = '<NoInteract state="True"></NoInteract>' if no_dialog else ''
        return f'<Step enable="True" id="75" name="Commit Records/Requests">{nd}</Step>'

    elif st == 'perform_script':
        name = _strip_outer_quotes(p.get('script_name', ''))
        param = p.get('parameter', '')
        xml = f'<Step enable="True" id="1" name="Perform Script">'
        if param:
            xml += f'<Calculation>{_cdata(param)}</Calculation>'
        xml += f'<Text>{name}</Text>'
        xml += '</Step>'
        return xml

    elif st == 'go_to_layout':
        layout = _strip_outer_quotes(p.get('layout', ''))
        if layout:
            return (f'<Step enable="True" id="6" name="Go to Layout">'
                    f'<LayoutDestination value="ByName"></LayoutDestination>'
                    f'<Layout id="0" name="{layout}"></Layout></Step>')
        else:
            return (f'<Step enable="True" id="6" name="Go to Layout">'
                    f'<LayoutDestination value="CurrentLayout"></LayoutDestination>'
                    f'<Layout id="0" name=""></Layout></Step>')

    elif st == 'insert_from_url':
        target = p.get('target', '')
        url = p.get('url', '')
        curl = p.get('curl', '')
        xml = f'<Step enable="True" id="160" name="Insert from URL">'
        xml += '<NoInteract state="False"></NoInteract>'
        xml += '<DontEncodeURL state="False"></DontEncodeURL>'
        xml += '<SelectAll state="False"></SelectAll>'
        xml += '<VerifySSLCertificates state="False"></VerifySSLCertificates>'
        if url:
            xml += f'<URL><Calculation>{_cdata(url)}</Calculation></URL>'
        if curl:
            xml += f'<CURLOptions><Calculation>{_cdata(curl)}</Calculation></CURLOptions>'
        xml += '</Step>'
        return xml

    elif st == 'go_to_record':
        direction = p.get('direction', 'First')
        return (f'<Step enable="True" id="16" name="Go to Record/Request/Page">'
                f'<RowPageLocation value="{direction}"></RowPageLocation>'
                f'<NoInteract state="False"></NoInteract></Step>')

    elif st == 'new_record':
        return '<Step enable="True" id="7" name="New Record/Request"></Step>'

    elif st == 'enter_find_mode':
        pause = p.get('pause', False)
        pause_state = 'True' if pause else 'False'
        return (f'<Step enable="True" id="22" name="Enter Find Mode">'
                f'<Pause state="{pause_state}"></Pause>'
                f'<Restore state="False"></Restore></Step>')

    elif st == 'perform_find':
        return '<Step enable="True" id="28" name="Perform Find"><Restore state="False"></Restore></Step>'

    elif st == 'sort_records':
        return '<Step enable="True" id="39" name="Sort Records"><NoInteract state="False"></NoInteract><Restore state="False"></Restore></Step>'

    else:
        return f'<!-- Unknown step: {st} -->'


def generate_xml(steps):
    """Generate complete fmxmlsnippet from validated steps."""
    xml_parts = ['<fmxmlsnippet type="FMObjectList">']
    for step in steps:
        xml = step_to_xml(step)
        if not step.enabled:
            xml = xml.replace('enable="True"', 'enable="False"', 1)
        xml_parts.append(xml)
    xml_parts.append('</fmxmlsnippet>')
    return ''.join(xml_parts)


# ============================================================
#  STAGE 4: DECOMPILER (XML → Plain Text)
# ============================================================

def decompile_xml(xml_text):
    """Convert FM XML snippet back to readable plain text."""
    import xml.etree.ElementTree as ET

    # Parse the XML
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        return f"XML Parse Error: {e}"

    lines = []
    indent_level = 0
    indent_str = "    "

    for step_elem in root.iter('Step'):
        step_id = step_elem.get('id', '')
        step_name = step_elem.get('name', '')
        enable = step_elem.get('enable', 'True')

        prefix = "" if enable == "True" else "// "

        # Decrease indent before End and Else
        if step_id in ('70', '73'):  # End If, End Loop
            indent_level = max(0, indent_level - 1)
        elif step_id in ('69', '125'):  # Else, Else If
            indent_level = max(0, indent_level - 1)

        pad = indent_str * indent_level

        # --- Comment ---
        if step_id == '89':
            text = _get_text(step_elem, 'Text') or ''
            # Multi-line comments: prefix each line with #
            comment_lines = text.split('\n')
            for cl in comment_lines:
                lines.append(f"{pad}{prefix}# {cl}")

        # --- Set Error Capture ---
        elif step_id == '86':
            set_elem = step_elem.find('Set')
            state = 'On' if set_elem is not None and set_elem.get('state') == 'True' else 'Off'
            lines.append(f"{pad}{prefix}Set Error Capture [ {state} ]")

        # --- Allow User Abort ---
        elif step_id == '85':
            set_elem = step_elem.find('Set')
            state = 'On' if set_elem is not None and set_elem.get('state') == 'True' else 'Off'
            lines.append(f"{pad}{prefix}Allow User Abort [ {state} ]")

        # --- Set Variable ---
        elif step_id == '141':
            name = _get_text(step_elem, 'Name') or '$?'
            value = _get_calc(step_elem, 'Value') or ''
            lines.append(f"{pad}{prefix}Set Variable [ {name} ; Value: {value} ]")

        # --- Set Field By Name ---
        elif step_id == '147':
            target = _get_calc(step_elem, 'TargetName') or '?'
            value = _get_calc(step_elem, 'Result') or ''
            lines.append(f"{pad}{prefix}Set Field By Name [ {target} ; {value} ]")

        # --- If ---
        elif step_id == '68':
            calc = _get_calc_direct(step_elem) or '?'
            lines.append(f"{pad}{prefix}If [ {calc} ]")
            indent_level += 1

        # --- Else If ---
        elif step_id == '125' and step_name == 'Else If':
            calc = _get_calc_direct(step_elem) or '?'
            lines.append(f"{pad}{prefix}Else If [ {calc} ]")
            indent_level += 1

        # --- Else ---
        elif step_id == '69':
            lines.append(f"{pad}{prefix}Else")
            indent_level += 1

        # --- End If ---
        elif step_id == '70' and step_name == 'End If':
            lines.append(f"{pad}{prefix}End If")

        # --- Loop ---
        elif step_id == '71' and step_name == 'Loop':
            lines.append(f"{pad}{prefix}Loop")
            indent_level += 1

        # --- Exit Loop If ---
        elif step_id == '72':
            calc = _get_calc_direct(step_elem) or '?'
            lines.append(f"{pad}{prefix}Exit Loop If [ {calc} ]")

        # --- End Loop ---
        elif step_id == '73':
            lines.append(f"{pad}{prefix}End Loop")

        # --- Show Custom Dialog ---
        elif step_id == '87':
            title = _get_calc(step_elem, 'Title') or '""'
            message = _get_calc(step_elem, 'Message') or '""'
            buttons_elem = step_elem.find('Buttons')
            btn_list = []
            if buttons_elem is not None:
                for btn in buttons_elem.findall('Button'):
                    calc_elem = btn.find('Calculation')
                    if calc_elem is not None and calc_elem.text:
                        btn_list.append(calc_elem.text.strip())
            btn_str = ' ; '.join(btn_list) if btn_list else '"OK"'
            parts = [title, message]
            if btn_list:
                parts.extend(btn_list)
            lines.append(f"{pad}{prefix}Show Custom Dialog [ {' ; '.join(parts)} ]")

        # --- Exit Script ---
        elif step_id == '103':
            calc = _get_calc_direct(step_elem) or ''
            if calc:
                lines.append(f"{pad}{prefix}Exit Script [ {calc} ]")
            else:
                lines.append(f"{pad}{prefix}Exit Script")

        # --- Commit Records ---
        elif step_id == '75':
            no_interact = step_elem.find('NoInteract')
            if no_interact is not None and no_interact.get('state') == 'True':
                lines.append(f"{pad}{prefix}Commit Records [ No dialog ]")
            else:
                lines.append(f"{pad}{prefix}Commit Records")

        # --- Perform Script ---
        elif step_id == '1':
            # Script name: prefer <Script name=>, fall back to <Text>
            script_elem = step_elem.find('Script')
            if script_elem is not None:
                name = script_elem.get('name', '') or _get_text(step_elem, 'Text') or '?'
            else:
                name = _get_text(step_elem, 'Text') or '?'
            # Parameter is direct <Calculation> child
            param = _get_calc_direct(step_elem) or ''
            if param:
                lines.append(f'{pad}{prefix}Perform Script [ "{name}" ; {param} ]')
            else:
                lines.append(f'{pad}{prefix}Perform Script [ "{name}" ]')

        # --- Go to Layout ---
        elif step_id == '6':
            layout_elem = step_elem.find('Layout')
            dest_elem = step_elem.find('LayoutDestination')
            name = layout_elem.get('name', '') if layout_elem is not None else ''
            dest = dest_elem.get('value', '') if dest_elem is not None else ''
            if dest == 'OriginalLayout':
                lines.append(f'{pad}{prefix}Go to Layout [ original layout ]')
            elif name:
                lines.append(f'{pad}{prefix}Go to Layout [ "{name}" ]')
            else:
                lines.append(f'{pad}{prefix}Go to Layout [ current layout ]')

        # --- Insert from URL ---
        elif step_id == '160':
            target = _get_calc(step_elem, 'Field') or ''
            url = _get_calc(step_elem, 'URL') or ''
            curl = _get_calc(step_elem, 'CURLOptions') or ''
            parts = []
            if target:
                parts.append(f'Target: {target}')
            if url:
                parts.append(f'URL: {url}')
            if curl:
                parts.append(f'cURL: {curl}')
            if parts:
                lines.append(f"{pad}{prefix}Insert from URL [ {' ; '.join(parts)} ]")
            else:
                lines.append(f"{pad}{prefix}Insert from URL")

        # --- Go to Record ---
        elif step_id == '16':
            dir_elem = step_elem.find('RowPageLocation')
            if dir_elem is not None:
                direction = dir_elem.get('value', '?')
            else:
                direction = '?'
            lines.append(f"{pad}{prefix}Go to Record/Request/Page [ {direction} ]")

        # --- New Record ---
        elif step_id == '7' and step_name == 'New Record/Request':
            lines.append(f"{pad}{prefix}New Record/Request")

        # --- Enter Find Mode ---
        elif step_id == '22':
            lines.append(f"{pad}{prefix}Enter Find Mode")

        # --- Perform Find ---
        elif step_id == '28':
            lines.append(f"{pad}{prefix}Perform Find")

        # --- Sort Records ---
        elif step_id == '39':
            lines.append(f"{pad}{prefix}Sort Records")

        # --- Set Field (76) ---
        elif step_id == '76':
            field_elem = step_elem.find('Field')
            table = field_elem.get('table', '') if field_elem is not None else ''
            fname = field_elem.get('name', '') if field_elem is not None else '?'
            calc = _get_calc_direct(step_elem) or ''
            field_ref = f'{table}::{fname}' if table else fname
            if calc:
                lines.append(f'{pad}{prefix}Set Field [ {field_ref} ; {calc} ]')
            else:
                lines.append(f'{pad}{prefix}Set Field [ {field_ref} ]')

        # --- Insert Text (61) ---
        elif step_id == '61':
            field_elem = step_elem.find('Field')
            field_target = field_elem.text.strip() if field_elem is not None and field_elem.text else ''
            text_content = _get_text(step_elem, 'Text') or ''
            select_all = step_elem.find('SelectAll')
            sa = select_all.get('state', 'False') if select_all is not None else 'False'
            preview = text_content[:80] + '...' if len(text_content) > 80 else text_content
            preview = preview.replace('\r', ' ').replace('\n', ' ')
            parts = []
            if sa == 'True':
                parts.append('Select All')
            if field_target:
                parts.append(f'Target: {field_target}')
            if preview:
                parts.append(f'"{preview}"')
            lines.append(f'{pad}{prefix}Insert Text [ {" ; ".join(parts)} ]')

        # --- New Window (122) ---
        elif step_id == '122':
            name_calc = _get_calc(step_elem, 'Name') or ''
            layout_elem = step_elem.find('Layout')
            layout_name = layout_elem.get('name', '') if layout_elem is not None else ''
            parts = []
            if name_calc:
                parts.append(f'Name: {name_calc}')
            if layout_name:
                parts.append(f'Layout: "{layout_name}"')
            style_elem = step_elem.find('NewWndStyles')
            if style_elem is not None:
                style = style_elem.get('Style', '')
                if style:
                    parts.append(f'Style: {style}')
            if parts:
                lines.append(f'{pad}{prefix}New Window [ {" ; ".join(parts)} ]')
            else:
                lines.append(f'{pad}{prefix}New Window')

        # --- Adjust Window (31) ---
        elif step_id == '31':
            ws_elem = step_elem.find('WindowState')
            state = ws_elem.get('value', '?') if ws_elem is not None else '?'
            lines.append(f'{pad}{prefix}Adjust Window [ {state} ]')

        # --- Refresh Window (80) ---
        elif step_id == '80':
            lines.append(f'{pad}{prefix}Refresh Window')

        # --- Halt Script (90) ---
        elif step_id == '90':
            lines.append(f'{pad}{prefix}Halt Script')

        # --- Configure LLM Template (226) ---
        elif step_id == '226':
            tmpl = step_elem.find('ConfigureLLMTemplate')
            if tmpl is not None:
                tname = _get_calc(tmpl, 'TemplateName') or ''
                provider_elem = tmpl.find('ModelProvider')
                provider = provider_elem.text.strip() if provider_elem is not None and provider_elem.text else ''
                parts = []
                if tname:
                    parts.append(f'Template: {tname}')
                if provider:
                    parts.append(f'Provider: {provider}')
                lines.append(f'{pad}{prefix}Configure LLM Template [ {" ; ".join(parts)} ]')
            else:
                lines.append(f'{pad}{prefix}Configure LLM Template')

        # --- LLM Request (214) ---
        elif step_id == '214':
            req = step_elem.find('LLMRequest')
            if req is not None:
                model = _get_calc(req, 'Model') or ''
                action_elem = req.find('Action')
                action = action_elem.text.strip() if action_elem is not None and action_elem.text else ''
                account = _get_calc(req, 'AccountName') or ''
                prompt = _get_calc(req, 'PromptMessage') or ''
                scope_elem = req.find('QueryScope')
                scope = scope_elem.text.strip() if scope_elem is not None and scope_elem.text else ''
                # Target field
                field_elem = step_elem.find('Field')
                target = ''
                if field_elem is not None:
                    t = field_elem.get('table', '')
                    n = field_elem.get('name', '')
                    target = f'{t}::{n}' if t else n
                # Stream state
                stream_elem = step_elem.find('Stream')
                stream = stream_elem.get('state', '') if stream_elem is not None else ''
                # Table aliases
                tables = []
                aliases = req.find('TableAliases')
                if aliases is not None:
                    for tbl in aliases.findall('Table'):
                        tables.append(tbl.get('name', ''))
                parts = []
                if action:
                    parts.append(f'Action: {action}')
                if model:
                    parts.append(f'Model: {model}')
                if account:
                    parts.append(f'Account: {account}')
                if prompt:
                    parts.append(f'Prompt: {prompt}')
                if target:
                    parts.append(f'Target: {target}')
                if stream == 'True':
                    parts.append('Stream: On')
                if scope:
                    parts.append(f'Scope: {scope}')
                if tables:
                    parts.append(f'Tables: {", ".join(tables)}')
                lines.append(f'{pad}{prefix}LLM Request [ {" ; ".join(parts)} ]')
            else:
                lines.append(f'{pad}{prefix}LLM Request')

        # --- Fallback ---
        else:
            lines.append(f"{pad}{prefix}{step_name} [id={step_id}]")

    return '\n'.join(lines)


def _get_text(elem, tag):
    """Get direct text content of a child element."""
    child = elem.find(tag)
    if child is not None:
        return (child.text or '').strip()
    return None


def _get_calc(elem, parent_tag):
    """Get calculation text from parent/Calculation structure."""
    parent = elem.find(parent_tag)
    if parent is not None:
        calc = parent.find('Calculation')
        if calc is not None:
            return (calc.text or '').strip()
    return None


def _get_calc_direct(elem):
    """Get calculation text directly under the element."""
    calc = elem.find('Calculation')
    if calc is not None:
        return (calc.text or '').strip()
    return None


# ============================================================
#  CLIPBOARD SUPPORT (macOS)
# ============================================================

def load_to_clipboard(xml_text, paste_type='XMSS'):
    """Load XML onto macOS clipboard as FileMaker pasteboard object.
    Tries PyObjC directly, then falls back to system Python subprocess."""
    import subprocess

    type_map = {
        'XMSS': 0x584D5353, 'XMSC': 0x584D5343,
        'XMFN': 0x584D464E, 'XMTB': 0x584D5442,
        'XMFD': 0x584D4644, 'XMVL': 0x584D564C,
        'XML2': 0x584D4C32,
    }

    # --- Attempt 1: PyObjC in current environment ---
    try:
        from AppKit import NSPasteboard, NSData

        hex_code = type_map.get(paste_type, 0x584D5353)
        flavor = f"CorePasteboardFlavorType 0x{hex_code:08X}"

        pb = NSPasteboard.generalPasteboard()
        pb.clearContents()
        xml_bytes = xml_text.encode('utf-8')
        ns_data = NSData.dataWithBytes_length_(xml_bytes, len(xml_bytes))
        pb.setData_forType_(ns_data, flavor)
        return True

    except ImportError:
        pass

    # --- Attempt 2: Inline system Python subprocess ---
    # Minimal PyObjC clipboard write as a one-liner via /usr/bin/python3
    try:
        clip_script = (
            "import sys; "
            "from AppKit import NSPasteboard, NSData; "
            "xml = sys.stdin.buffer.read(); "
            f"flavor = 'CorePasteboardFlavorType 0x{type_map.get(paste_type, 0x584D5353):08X}'; "
            "pb = NSPasteboard.generalPasteboard(); "
            "pb.clearContents(); "
            "ns_data = NSData.dataWithBytes_length_(xml, len(xml)); "
            "pb.setData_forType_(ns_data, flavor)"
        )
        result = subprocess.run(
            ['/usr/bin/python3', '-c', clip_script],
            input=xml_text.encode('utf-8'),
            capture_output=True, text=False, timeout=10
        )
        if result.returncode == 0:
            print("  (loaded via /usr/bin/python3 subprocess)")
            return True
        else:
            stderr = result.stderr.decode('utf-8', errors='replace')
            print(f"  System Python failed: {stderr.strip()}")
    except Exception as e:
        print(f"  System Python error: {e}")

    print("  ERROR: Could not load clipboard. Install PyObjC or use /usr/bin/python3 directly.")
    return False


def read_clipboard_text():
    """Read plain text from macOS clipboard."""
    try:
        from AppKit import NSPasteboard, NSPasteboardTypeString
        pb = NSPasteboard.generalPasteboard()
        text = pb.stringForType_(NSPasteboardTypeString)
        return text
    except ImportError:
        # Fallback to pbpaste
        import subprocess
        result = subprocess.run(['pbpaste'], capture_output=True, text=True)
        return result.stdout


def read_clipboard_fm():
    """Read FM XML from macOS clipboard (XMSS type)."""
    try:
        from AppKit import NSPasteboard, NSData
        pb = NSPasteboard.generalPasteboard()

        # Try XMSS first, then XMSC
        for hex_code in [0x584D5353, 0x584D5343]:
            flavor = f"CorePasteboardFlavorType 0x{hex_code:08X}"
            data = pb.dataForType_(flavor)
            if data:
                return bytes(data).decode('utf-8'), 'XMSS' if hex_code == 0x584D5353 else 'XMSC'

        return None, None
    except ImportError:
        print("  ERROR: PyObjC not available.")
        return None, None


# ============================================================
#  CLI
# ============================================================

def print_banner(mode):
    print("=" * 60)
    print(f"  FM_CP — FileMaker Compose / Parse  v{__version__}")
    print(f"  Mode: {mode}")
    print("=" * 60)


def _is_fm_xml(text):
    """Detect if text is FM XML content."""
    t = text.strip()
    return t.startswith('<fmxmlsnippet') or t.startswith('<?xml')


def cmd_process(args):
    """Unified processor: auto-detect input, route accordingly."""
    clipboard_mode = '-c' in args or '--clipboard' in args
    output_file = None
    file_path = None

    # Parse -o/--output with its value
    skip_next = False
    for i, a in enumerate(args):
        if skip_next:
            skip_next = False
            continue
        if a in ('-o', '--output') and i + 1 < len(args):
            output_file = args[i + 1]
            skip_next = True
        elif a not in ('-c', '--clipboard'):
            file_path = a

    # --- Get input ---
    if clipboard_mode:
        # Try FM pasteboard first
        xml_text, fm_type = read_clipboard_fm()
        if xml_text:
            content = xml_text
            source = f"clipboard ({fm_type})"
        else:
            content = read_clipboard_text()
            source = "clipboard"
        if not content:
            print_banner("clipboard")
            print("  ✗ ERROR: No content found on clipboard")
            print("=" * 60)
            sys.exit(1)
    elif file_path:
        with open(file_path, 'r') as f:
            content = f.read()
        source = file_path
    else:
        # No args — try clipboard as default
        xml_text, fm_type = read_clipboard_fm()
        if xml_text:
            content = xml_text
            source = f"clipboard ({fm_type})"
            clipboard_mode = True
        else:
            content = read_clipboard_text()
            if content:
                source = "clipboard"
                clipboard_mode = True
            else:
                print_banner("fm_cp")
                print("  Usage: fm-cp [-c] [-o file] [input]")
                print()
                print("  Auto-detects input format:")
                print("    Plain text → compose → FM XML to clipboard")
                print("    FM XML     → decompile → plain text to clipboard")
                print()
                print("  Options:")
                print("    -c          Read from clipboard")
                print("    -o file     Write output to file instead of clipboard")
                print("=" * 60)
                sys.exit(0)

    is_xml = _is_fm_xml(content)

    # --- Route ---
    if is_xml:
        # XML → decompile to readable plain text
        print_banner(f"decompile — {source}")
        plain_text = decompile_xml(content)
        print()
        print(plain_text)
        print()
        print("-" * 60)
        print(f"  {plain_text.count(chr(10)) + 1} lines decompiled")

        if output_file:
            with open(output_file, 'w') as f:
                f.write(plain_text + '\n')
            print(f"  → Saved to {output_file}")
        else:
            # Copy plain text to clipboard for pasting to Claude
            try:
                import subprocess
                subprocess.run(['pbcopy'], input=plain_text.encode('utf-8'))
                print("  (Plain text copied to clipboard — paste to Claude)")
            except Exception:
                pass
        print("=" * 60)

    else:
        # Plain text → compose → FM clipboard
        print_banner(f"compose — {source}")

        # Stage 1: Parse
        steps, parse_errors = parse_text(content)

        if parse_errors:
            print()
            print("  PARSE ERRORS:")
            for e in parse_errors:
                print(f"  ✗ {e}")
            print()
            print(f"  ✗ FAIL — {len(parse_errors)} parse error(s)")
            print("=" * 60)
            sys.exit(1)

        print(f"  ✓ Parsed {len(steps)} step(s)")

        # Stage 2: Validate
        val_result = validate_structure(steps)

        if not val_result.is_valid:
            print()
            print(val_result.report())
            sys.exit(1)

        if val_result.warnings:
            for w in val_result.warnings:
                print(f"  ⚠ {w}")

        print(f"  ✓ Structure valid")

        # Stage 3: Generate XML
        xml = generate_xml(steps)
        print(f"  ✓ Generated XML ({len(xml)} bytes, {len(steps)} steps)")

        # Load to clipboard or save to file
        if output_file:
            with open(output_file, 'w') as f:
                f.write(xml)
            print(f"  → Saved to {output_file}")
        elif load_to_clipboard(xml):
            print()
            print("  CLIPBOARD LOADED")
            print("  Type:  XMSS (Script Steps)")
            print(f"  Size:  {len(xml):,} bytes")
            print()
            print("  Cmd+V into FileMaker Script Workspace to paste.")
        else:
            print()
            print("  Could not load clipboard. XML output:")
            print(xml)

        print("=" * 60)


def cmd_dump(args=None):
    """Dump raw FM XML from clipboard for debugging."""
    output_file = None
    if args:
        for i, a in enumerate(args):
            if a in ('-o', '--output') and i + 1 < len(args):
                output_file = args[i + 1]

    print_banner("dump — raw clipboard XML")
    xml_text, fm_type = read_clipboard_fm()
    if xml_text:
        print(f"  Type: {fm_type}")
        print(f"  Size: {len(xml_text)} bytes")
        print()
        print(xml_text)
        if output_file:
            with open(output_file, 'w') as f:
                f.write(xml_text)
            print()
            print(f"  → Saved to {output_file}")
    else:
        print("  No FM data on clipboard.")
        print("  Copy steps from FM Script Workspace (Cmd+C) first.")
    print("=" * 60)


def main():
    args = sys.argv[1:]

    if args and args[0] in ('-v', '--version'):
        print(f"fm-cp {__version__}")
        return

    if not args or args[0] in ('-h', '--help'):
        print_banner("fm_cp")
        print("  Usage: fm-cp [-c] [-o file] [input]")
        print()
        print("  Auto-detects input format:")
        print("    Plain text on clipboard → compose → FM XML to clipboard (Cmd+V into FM)")
        print("    FM XML on clipboard     → decompile → plain text to clipboard (paste to Claude)")
        print()
        print("  Options:")
        print("    -c          Read from clipboard")
        print("    -o file     Write output to file instead of clipboard")
        print()
        print("  Examples:")
        print("    fm-cp -c                 # Auto-detect clipboard, convert either direction")
        print("    fm-cp script.txt         # Compose file → FM clipboard")
        print("    fm-cp script.xml         # Decompile XML file → plain text to clipboard")
        print("    fm-cp script.txt -o out.xml   # Compose → save XML file")
        print("    fm-cp script.xml -o out.txt   # Decompile → save text file")
        print("    fm-cp dump               # Raw clipboard XML dump")
        print("    fm-cp dump -o raw.xml    # Dump clipboard → file")
        print("=" * 60)
        return

    if args[0].lower() == 'dump':
        cmd_dump(args[1:])
        return

    cmd_process(args)


if __name__ == '__main__':
    main()
