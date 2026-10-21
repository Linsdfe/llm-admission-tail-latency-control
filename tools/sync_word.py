from pathlib import Path
from copy import deepcopy
from difflib import SequenceMatcher
import re
import json
from docx import Document
from docx.text.paragraph import Paragraph
from docx.table import Table
from docx.shared import Pt, Cm
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT

ROOT = Path(__file__).resolve().parent.parent
BACK = ROOT

def plain(s):
    s = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', lambda m: m[1] + '（' + m[2] + '）', s)
    s = re.sub(r'\*\*(.+?)\*\*', r'\1', s)
    return s.replace('`', '').strip()

def blocks_from_md(text):
    lines = text.splitlines()
    blocks = []
    i = 0
    before_body = True
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        if not line or line == '---':
            continue
        if line.startswith('|'):
            rows = []
            while True:
                cells = [plain(x) for x in line.strip('|').split('|')]
                if not all(re.fullmatch(r':?-+:?', x.strip()) for x in cells):
                    rows.append(cells)
                if i >= len(lines) or not lines[i].strip().startswith('|'):
                    break
                line = lines[i].strip()
                i += 1
            assert len({len(row) for row in rows}) == 1
            blocks.append(('table', rows, ''))
            continue
        heading = re.match(r'^(#{1,6})\s+(.*)', line)
        if heading:
            level = len(heading[1])
            title = plain(heading[2])
            if level == 1:
                style = 'Title'
            elif before_body and title in ('本科毕业设计开题报告', '项目开发流程计划'):
                style = 'Subtitle'
            else:
                before_body = False
                style = f'Heading {level-1}'
            blocks.append(('p', title, style))
        else:
            style = 'Cover Text' if before_body else 'Normal'
            if re.match(r'^\[\d+\]', line):
                style = 'Reference'
            blocks.append(('p', plain(line), style))
    return blocks

def key(block):
    if block[0] == 'table':
        return ('table', tuple(tuple(re.sub(r'\s+', '', c) for c in row) for row in block[1]))
    return ('p', re.sub(r'\s+', '', block[1]))

def existing_blocks(doc):
    result, elements = [], []
    for el in doc.element.body:
        if el.tag == qn('w:p'):
            p = Paragraph(el, doc)
            if not p.text.strip():
                continue
            result.append(('p', p.text.strip(), p.style.name))
        elif el.tag == qn('w:tbl'):
            t = Table(el, doc)
            result.append(('table', [[c.text.strip() for c in row.cells] for row in t.rows], ''))
        else:
            continue
        elements.append(el)
    return result, elements

def author_block(doc, block, table_templates):
    kind, data, style = block
    if kind == 'p':
        if style not in doc.styles:
            style = 'Normal'
        p = doc.add_paragraph(data, style=style)
        if style.startswith('Heading') or style == 'Title':
            for run in p.runs:
                run.font.color.rgb = __import__('docx.shared', fromlist=['RGBColor']).RGBColor(0, 0, 0)
        # Long literal reference URLs must be allowed to wrap normally.
        p.paragraph_format.widow_control = True
        return p._p
    ncol = len(data[0])
    table = doc.add_table(rows=len(data), cols=ncol)
    template = table_templates.get(ncol)
    if template is not None:
        table._tbl.remove(table._tbl.tblPr)
        table._tbl.insert(0, deepcopy(template._tbl.tblPr))
        table._tbl.remove(table._tbl.tblGrid)
        table._tbl.insert(1, deepcopy(template._tbl.tblGrid))
        widths = [c.width for c in template.rows[0].cells]
        for row in table.rows:
            for j,c in enumerate(row.cells):
                c.width = widths[j]
    else:
        table.style = 'Table Grid'
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    # Fit short identifiers and allocate width to narrative columns.
    header = data[0]
    widths_by_header = {
        ('编号', '需求', '主要证据'): [2.1, 5.3, 9.1],
        ('模块', '职责'): [4.1, 12.4],
        ('项目', '已确认配置'): [3.8, 12.7],
        ('编号', '策略', '比较目的'): [2.6, 7.5, 6.4],
        ('指标', '口径'): [3.8, 12.7],
        ('批次', '已验证观察', '解释边界'): [3.0, 7.0, 6.5],
        ('阶段', '日期', '人时', '产出与决策门'): [3.2, 5.1, 1.3, 6.9],
        ('触发与节点', '保留内容', '处置与重新验收'): [4.6, 3.7, 8.2],
        ('编号', '验收项', '通过证据'): [1.5, 3.5, 11.5],
        ('需求', '模块', '验收与实验', '论文章节'): [1.5, 3.8, 8.6, 2.6],
    }
    chosen_widths = widths_by_header.get(tuple(header))
    if chosen_widths:
        for j, width in enumerate(chosen_widths):
            table.columns[j].width = Cm(width)
            for row in table.rows:
                row.cells[j].width = Cm(width)
    for i,row in enumerate(table.rows):
        # Avoid a row breaking between pages while allowing its height to expand.
        trpr = row._tr.get_or_add_trPr()
        nosplit = OxmlElement('w:cantSplit')
        trpr.append(nosplit)
        if i == 0:
            repeat = OxmlElement('w:tblHeader')
            trpr.append(repeat)
        for j,c in enumerate(row.cells):
            c.text = data[i][j]
            c.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            tcpr = c._tc.get_or_add_tcPr()
            mar = OxmlElement('w:tcMar')
            for edge, width in [('top',80),('bottom',80),('left',90),('right',90)]:
                e = OxmlElement('w:'+edge)
                e.set(qn('w:w'),str(width)); e.set(qn('w:type'),'dxa'); mar.append(e)
            tcpr.append(mar)
            if i == 0:
                shade = OxmlElement('w:shd'); shade.set(qn('w:fill'),'E8EDF2'); tcpr.append(shade)
            for p in c.paragraphs:
                p.paragraph_format.first_line_indent = Pt(0)
                p.paragraph_format.space_before = Pt(0)
                p.paragraph_format.space_after = Pt(3)
                p.paragraph_format.line_spacing = 1.15
                p.paragraph_format.keep_with_next = (i == 0)
                for run in p.runs:
                    run.font.size = Pt(10)
                    run.font.bold = (i == 0)
                    run.font.name = 'Times New Roman'
                    run._element.get_or_add_rPr().rFonts.set(qn('w:eastAsia'), '宋体')
    borders = table._tbl.tblPr.find(qn('w:tblBorders'))
    if borders is None:
        borders = OxmlElement('w:tblBorders'); table._tbl.tblPr.append(borders)
    for edge in ('top','left','bottom','right','insideH','insideV'):
        b = borders.find(qn('w:'+edge))
        if b is None:
            b = OxmlElement('w:'+edge); borders.append(b)
        for attr,val in [('val','single'),('sz','4'),('color','D9D9D9')]:
            b.set(qn('w:'+attr),val)
    return table._tbl

reports = []
for stem in ['开题报告_面向混合长短请求的LLM推理准入与尾延迟控制系统', '开发流程计划_面向混合长短请求的LLM推理准入与尾延迟控制系统']:
    dest = ROOT / (stem + '.docx')
    doc = Document(BACK / dest.name)
    oldblocks, oldelements = existing_blocks(doc)
    source_text = (ROOT/(stem+'.md')).read_text(encoding='utf-8')
    version_match = re.search(r'文档版本：V([0-9.]+)', source_text)
    assert version_match, 'Document version missing: ' + stem
    target = blocks_from_md(source_text)
    templates = {len(t.columns): t for t in doc.tables}
    matcher = SequenceMatcher(None, [key(b) for b in oldblocks], [key(b) for b in target], autojunk=False)
    output = []
    reused = 0
    for op,a,b,c,d in matcher.get_opcodes():
        if op == 'equal':
            output += oldelements[a:b]
            reused += b-a
        elif op in ('insert','replace'):
            for block in target[c:d]:
                output.append(author_block(doc,block,templates))
    # Preserve cover-page separation without retaining unrelated empty paragraphs.
    first_body = next((i for i,b in enumerate(target) if b[0]=='p' and b[2]=='Heading 1'),None)
    if first_body is not None:
        p = Paragraph(output[first_body], doc)
        p.paragraph_format.page_break_before = True
    sect = deepcopy(doc.element.body.sectPr)
    for child in list(doc.element.body):
        doc.element.body.remove(child)
    for el in output:
        doc.element.body.append(el)
    doc.element.body.append(sect)
    doc.core_properties.version = version_match[1]
    if stem.startswith('开发流程计划'):
        # Keep the short final delivery checklist on the preceding page without
        # reducing the source document's font size or line spacing.
        doc.styles['Normal'].paragraph_format.space_after = Pt(3.5)
        doc.styles['Heading 1'].paragraph_format.space_before = Pt(10)
        doc.styles['Heading 2'].paragraph_format.space_before = Pt(10)
    # The compact module overview should remain a single readable table.
    for table in doc.tables:
        table_header = [cell.text for cell in table.rows[0].cells]
        if table_header == ['编号', '策略', '比较目的']:
            for j, width in enumerate([2.6, 7.5, 6.4]):
                table.columns[j].width = Cm(width)
                for row in table.rows:
                    row.cells[j].width = Cm(width)
        if table_header == ['模块', '职责']:
            for row in table.rows[:-1]:
                for cell in row.cells:
                    for paragraph in cell.paragraphs:
                        paragraph.paragraph_format.keep_with_next = True
    doc.save(dest)
    actual,_ = existing_blocks(Document(dest))
    assert [key(b) for b in actual] == [key(b) for b in target], dest
    reports.append({'file':dest.name,'version':version_match[1],'blocks':len(target),'reused_blocks':reused,'tables':len(Document(dest).tables),'content_matches_markdown':True,'visual_qa':'pending_renderer'})
(ROOT/'tools/word_sync_checks.json').write_text(json.dumps(reports,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(reports,ensure_ascii=False))
