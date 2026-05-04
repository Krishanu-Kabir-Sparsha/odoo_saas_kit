"""Script to build SSLCOMMERZ_MIGRATION_GUIDE.html from the .md file"""
import re, html as h

md = open('SSLCOMMERZ_MIGRATION_GUIDE.md', 'r', encoding='utf-8').read()

CSS = """
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Segoe UI',system-ui,-apple-system,sans-serif;background:#0f172a;color:#e2e8f0;line-height:1.7;padding:0}
.wrapper{max-width:1100px;margin:0 auto;padding:40px 30px}
h1{font-size:2.2em;background:linear-gradient(135deg,#38bdf8,#818cf8);-webkit-background-clip:text;-webkit-text-fill-color:transparent;margin-bottom:8px;font-weight:800}
h2{font-size:1.6em;color:#38bdf8;margin:40px 0 16px;padding-bottom:8px;border-bottom:2px solid #1e3a5f}
h3{font-size:1.2em;color:#a78bfa;margin:28px 0 12px}
h4{font-size:1.05em;color:#67e8f9;margin:20px 0 8px}
p{margin:10px 0;color:#cbd5e1}
a{color:#38bdf8;text-decoration:none}
a:hover{text-decoration:underline}
ul,ol{margin:10px 0 10px 28px;color:#cbd5e1}
li{margin:4px 0}
strong{color:#f1f5f9}
code{background:#1e293b;color:#fbbf24;padding:2px 6px;border-radius:4px;font-size:0.9em;font-family:'Cascadia Code','Fira Code',monospace}
pre{background:#1e293b;border:1px solid #334155;border-radius:8px;padding:16px 20px;overflow-x:auto;margin:14px 0;font-size:0.88em;line-height:1.6}
pre code{background:none;padding:0;color:#e2e8f0}
.diff-add{color:#4ade80}
.diff-del{color:#f87171}
table{width:100%;border-collapse:collapse;margin:14px 0;font-size:0.92em}
th{background:#1e3a5f;color:#38bdf8;padding:10px 14px;text-align:left;font-weight:600;border:1px solid #334155}
td{padding:9px 14px;border:1px solid #334155;color:#cbd5e1;background:#0f172a}
tr:nth-child(even) td{background:#1a2332}
tr:hover td{background:#1e3a5f55}
blockquote{border-left:4px solid #f59e0b;background:#1e293b;padding:14px 20px;margin:16px 0;border-radius:0 8px 8px 0}
blockquote strong{color:#fbbf24}
hr{border:none;height:1px;background:linear-gradient(90deg,transparent,#334155,transparent);margin:30px 0}
.badge{display:inline-block;padding:4px 14px;border-radius:20px;font-size:0.82em;font-weight:600}
.badge-green{background:#065f4620;color:#4ade80;border:1px solid #4ade8040}
.header-bar{background:linear-gradient(135deg,#1e293b,#0f172a);border:1px solid #334155;border-radius:12px;padding:30px;margin-bottom:30px}
.header-bar p{color:#94a3b8;font-size:0.95em;margin:4px 0}
.toc{background:#1e293b;border:1px solid #334155;border-radius:10px;padding:20px 28px;margin:20px 0}
.toc a{display:block;padding:4px 0;color:#94a3b8;font-size:0.93em}
.toc a:hover{color:#38bdf8}
.flow-box{background:#1e293b;border:1px solid #334155;border-radius:10px;padding:20px;margin:14px 0;font-family:'Cascadia Code','Fira Code',monospace;font-size:0.85em;line-height:1.8;white-space:pre;overflow-x:auto;color:#67e8f9}
.alert{padding:14px 20px;border-radius:8px;margin:14px 0;font-size:0.93em}
.alert-warn{background:#78350f30;border:1px solid #f59e0b40;color:#fcd34d}
.alert-info{background:#0c4a6e30;border:1px solid #38bdf840;color:#7dd3fc}
.alert-danger{background:#7f1d1d30;border:1px solid #f8717140;color:#fca5a5}
.checklist{list-style:none;margin-left:0;padding-left:0}
.checklist li{padding:6px 0;padding-left:28px;position:relative}
.checklist li::before{content:'☐';position:absolute;left:0;color:#64748b}
@media print{body{background:#fff;color:#1e293b}td,th{color:#1e293b}pre{background:#f1f5f9;border-color:#cbd5e1}}
"""

def process_md(text):
    lines = text.split('\n')
    out = []
    in_code = False
    code_lang = ''
    code_buf = []
    in_table = False
    table_buf = []
    toc_items = []

    def flush_table():
        nonlocal table_buf, in_table
        if not table_buf:
            return ''
        rows = [r for r in table_buf if not re.match(r'^\|[\s\-\|]+\|$', r)]
        html_out = '<table>'
        for i, row in enumerate(rows):
            cells = [c.strip() for c in row.strip('|').split('|')]
            tag = 'th' if i == 0 else 'td'
            html_out += '<tr>' + ''.join(f'<{tag}>{fmt(c)}</{tag}>' for c in cells) + '</tr>'
        html_out += '</table>'
        table_buf = []
        in_table = False
        return html_out

    def fmt(t):
        t = h.escape(t)
        t = re.sub(r'`([^`]+)`', r'<code>\1</code>', t)
        t = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', t)
        t = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2" target="_blank">\1</a>', t)
        return t

    for line in lines:
        # Code blocks
        if line.startswith('```'):
            if in_code:
                code_text = '\n'.join(code_buf)
                if code_lang == 'diff':
                    colored = []
                    for cl in code_buf:
                        ec = h.escape(cl)
                        if cl.startswith('+'):
                            colored.append(f'<span class="diff-add">{ec}</span>')
                        elif cl.startswith('-'):
                            colored.append(f'<span class="diff-del">{ec}</span>')
                        else:
                            colored.append(ec)
                    out.append(f'<pre><code>{"<br>".join(colored)}</code></pre>')
                else:
                    out.append(f'<pre><code>{h.escape(code_text)}</code></pre>')
                code_buf = []
                in_code = False
            else:
                if in_table:
                    out.append(flush_table())
                in_code = True
                code_lang = line[3:].strip()
            continue

        if in_code:
            code_buf.append(line)
            continue

        # Tables
        if line.strip().startswith('|'):
            in_table = True
            table_buf.append(line)
            continue
        elif in_table:
            out.append(flush_table())

        # Headers
        m = re.match(r'^(#{1,4})\s+(.+)', line)
        if m:
            level = len(m.group(1))
            text = m.group(2)
            anchor = re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')
            if level == 2:
                toc_items.append((anchor, text))
            out.append(f'<h{level} id="{anchor}">{fmt(text)}</h{level}>')
            continue

        # Blockquotes
        if line.startswith('>'):
            content = line.lstrip('> ').strip()
            if '**IMPORTANT' in content or '**CRITICAL' in content:
                out.append(f'<div class="alert alert-warn">{fmt(content)}</div>')
            elif '**Do NOT' in content or '**DANGER' in content:
                out.append(f'<div class="alert alert-danger">{fmt(content)}</div>')
            else:
                out.append(f'<blockquote>{fmt(content)}</blockquote>')
            continue

        # Horizontal rules
        if line.strip() == '---':
            out.append('<hr>')
            continue

        # Checklist
        if line.strip().startswith('- [ ]'):
            text = line.strip()[5:].strip()
            out.append(f'<div style="padding:4px 0 4px 28px;position:relative"><span style="position:absolute;left:0;color:#64748b">☐</span> {fmt(text)}</div>')
            continue

        # List items
        m2 = re.match(r'^(\s*)[-*]\s+(.+)', line)
        if m2:
            out.append(f'<li>{fmt(m2.group(2))}</li>')
            continue
        m3 = re.match(r'^(\s*)\d+\.\s+(.+)', line)
        if m3:
            out.append(f'<li>{fmt(m3.group(2))}</li>')
            continue

        # Empty lines
        if not line.strip():
            out.append('')
            continue

        # Regular paragraphs
        out.append(f'<p>{fmt(line)}</p>')

    if in_table:
        out.append(flush_table())

    # Build TOC
    toc_html = '<div class="toc"><h3 style="color:#38bdf8;margin-bottom:10px">📑 Table of Contents</h3>'
    for anchor, title in toc_items:
        clean = re.sub(r'^\d+\.\s*', '', title)
        toc_html += f'<a href="#{anchor}">→ {clean}</a>'
    toc_html += '</div>'

    return toc_html, '\n'.join(out)

toc, body = process_md(md)

html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SSLCommerz Migration Guide — Odoo SaaS Kit</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrapper">
<div class="header-bar">
<h1>🔒 SSLCommerz Payment Gateway Migration</h1>
<p><strong>Project:</strong> Odoo 18 SaaS Kit &nbsp;|&nbsp; <strong>Migration:</strong> Stripe → SSLCommerz</p>
<p><strong>Date:</strong> May 5, 2026 &nbsp;|&nbsp; <strong>Status:</strong> <span class="badge badge-green">✅ COMPLETE</span></p>
</div>
{toc}
{body}
<hr>
<p style="text-align:center;color:#64748b;margin-top:30px">
<em>SSLCommerz Migration Guide — Odoo SaaS Kit — Generated May 5, 2026</em>
</p>
</div>
</body>
</html>"""

with open('SSLCOMMERZ_MIGRATION_GUIDE.html', 'w', encoding='utf-8') as f:
    f.write(html)
print(f'HTML file created: {len(html)} bytes')

