"""Build the three user manuals from the adjacent Markdown and browser screenshots."""
from pathlib import Path
import re
from html import escape
import base64
from reportlab.pdfgen import canvas
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, PageBreak, KeepTogether
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = ROOT / 'output/pdf'
OUT.mkdir(parents=True, exist_ok=True)
FONT = Path('C:/Windows/Fonts')
pdfmetrics.registerFont(TTFont('Guide', str(FONT/'arial.ttf')))
pdfmetrics.registerFont(TTFont('GuideBold', str(FONT/'arialbd.ttf')))
pdfmetrics.registerFontFamily('Guide',normal='Guide',bold='GuideBold',italic='Guide',boldItalic='GuideBold')
GREEN=colors.HexColor('#28756A')
INK=colors.HexColor('#263238')
GRAY=colors.HexColor('#63726F')

def inline(text):
    text=escape(text).replace('—','-').replace('–','-').replace('\u2011','-')
    text=re.sub(r'`([^`]+)`',r'<font color="#205F57">\1</font>',text)
    text=re.sub(r'\*\*([^*]+)\*\*',r'<b>\1</b>',text)
    return text

class NumberedCanvas(canvas.Canvas):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs);self.states=[]
    def showPage(self):
        self.states.append(dict(self.__dict__));self._startPage()
    def save(self):
        total=len(self.states)
        for state in self.states:
            self.__dict__.update(state)
            self.setFont('Guide',8)
            self.setFillColor(GRAY)
            self.drawString(36,24,'СВОД  /  ИНСТРУКЦИИ  /  12.09.2026')
            self.drawRightString(A4[0]-36,24,f'{self._pageNumber} / {total}')
            super().showPage()
        super().save()

def build(name, admin=False):
    lines=(HERE/f'{name}.md').read_text(encoding='utf-8').splitlines()
    size=10.5 if admin or name=='owner' else 9
    lead=14.2 if admin or name=='owner' else 11.5
    styles={
        'body':ParagraphStyle('body',fontName='Guide',fontSize=size,leading=lead,textColor=INK,spaceAfter=7 if admin else 4),
        'h1':ParagraphStyle('h1',fontName='GuideBold',fontSize=27 if admin else 22,leading=31,textColor=GREEN,spaceAfter=12),
        'h2':ParagraphStyle('h2',fontName='GuideBold',fontSize=18 if admin else 12,leading=23 if admin else 15,textColor=GREEN,spaceBefore=0 if admin else 6,spaceAfter=10 if admin else 4,keepWithNext=True),
        'caption':ParagraphStyle('caption',fontName='Guide',fontSize=8,leading=10,textColor=GRAY,spaceBefore=6,spaceAfter=8),
        'cell':ParagraphStyle('cell',fontName='Guide',fontSize=9 if admin else 8.5,leading=12 if admin else 10.5,textColor=INK),
    }
    story=[];i=0;first_section=True
    while i<len(lines):
        line=lines[i].strip();i+=1
        if not line: continue
        if line.startswith('# '):
            story.append(Paragraph(inline(line[2:]),styles['h1']));continue
        if line.startswith('## '):
            if admin and not first_section:story.append(PageBreak())
            first_section=False
            story.append(Paragraph(inline(line[3:]),styles['h2']));continue
        match=re.fullmatch(r'!\[(.*?)\]\((.*?)\)',line)
        if match:
            img=Image(str(HERE/match[2]))
            scale=min((A4[0]-72)/img.imageWidth,420/img.imageHeight)
            img.drawWidth=img.imageWidth*scale;img.drawHeight=img.imageHeight*scale
            img.hAlign='CENTER'
            story.append(KeepTogether([Spacer(1,8),img,Paragraph(inline(match[1])+'. Демонстрационные данные.',styles['caption'])]));continue
        if line.startswith('|'):
            rows=[line]
            while i<len(lines) and lines[i].strip().startswith('|'):
                rows.append(lines[i].strip());i+=1
            cells=[]
            for idx,row in enumerate(rows):
                parts=[p.strip() for p in row.strip('|').split('|')]
                if all(re.fullmatch(r':?-+:?',p) for p in parts):continue
                cells.append([Paragraph(('<b>'+inline(p)+'</b>') if idx==0 else inline(p),styles['cell']) for p in parts])
            widths=[155,A4[0]-72-155] if len(cells[0])==2 else None
            t=Table(cells,colWidths=widths,repeatRows=1,hAlign='LEFT')
            t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#E8F2EF')),('VALIGN',(0,0),(-1,-1),'TOP'),('GRID',(0,0),(-1,-1),0.35,colors.HexColor('#D5DEDB')),('LEFTPADDING',(0,0),(-1,-1),7),('RIGHTPADDING',(0,0),(-1,-1),7),('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5)]))
            story.extend([t,Spacer(1,7)]);continue
        if line.startswith('- '):line='• '+line[2:]
        story.append(Paragraph(inline(line),styles['body']))
    doc=SimpleDocTemplate(str(OUT/f'{name}.pdf'),pagesize=A4,rightMargin=36,leftMargin=36,topMargin=34,bottomMargin=43,title={'admin':'Свод: руководство администратора','owner':'Свод: памятка владельцу','staff':'Свод: памятка сотруднику'}[name],author='Свод',pageCompression=1)
    doc.build(story,canvasmaker=NumberedCanvas)
    print(OUT/f'{name}.pdf')

def build_html():
    # A self-contained reading copy keeps original screenshots easy to enlarge.
    raw=(HERE/'admin.md').read_text(encoding='utf-8').splitlines()
    body=[];toc=[];i=0
    while i<len(raw):
        line=raw[i].strip();i+=1
        if not line:continue
        if line.startswith('# '):body.append('<h1>'+escape(line[2:])+'</h1>');continue
        if line.startswith('## '):
            key='s'+str(len(toc)+1);label=escape(line[3:]);toc.append(f'<a href="#{key}">{label}</a>');body.append(f'<h2 id="{key}">{label}</h2>');continue
        m=re.fullmatch(r'!\[(.*?)\]\((.*?)\)',line)
        if m:
            data=base64.b64encode((HERE/m[2]).read_bytes()).decode()
            body.append(f'<figure><img src="data:image/png;base64,{data}" alt="{escape(m[1])}"><figcaption>{escape(m[1])}. Демонстрационные данные.</figcaption></figure>');continue
        if line.startswith('|'):
            rows=[line]
            while i<len(raw) and raw[i].strip().startswith('|'):rows.append(raw[i].strip());i+=1
            body.append('<table>')
            for j,row in enumerate(rows):
                cells=[c.strip() for c in row.strip('|').split('|')]
                if all(re.fullmatch(r':?-+:?',c) for c in cells):continue
                tag='th' if j==0 else 'td';body.append('<tr>'+''.join(f'<{tag}>{inline(c)}</{tag}>' for c in cells)+'</tr>')
            body.append('</table>');continue
        body.append('<p>'+inline(line)+'</p>')
    html='<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Свод: руководство администратора</title><style>body{font:17px/1.6 Arial,sans-serif;color:#263238;background:#f4f7f6;margin:0}main{max-width:1100px;margin:auto;background:white;padding:30px 40px}h1,h2,a{color:#28756a}h2{margin-top:55px;border-top:1px solid #dbe5e1;padding-top:25px}p{max-width:900px}img{width:100%;height:auto;border:1px solid #dbe5e1}figure{margin:24px 0}figcaption{font-size:14px;color:#63726f}nav{display:grid;grid-template-columns:1fr 1fr;gap:6px}nav a{font-size:14px;text-decoration:none}table{border-collapse:collapse;width:100%;margin:20px 0}th,td{padding:10px;border:1px solid #dbe5e1;text-align:left}th{background:#e8f2ef}@media(max-width:700px){main{padding:16px}nav{grid-template-columns:1fr}}</style><main><details><summary>Оглавление</summary><nav>'+''.join(toc)+'</nav></details>'+''.join(body)+'</main></html>'
    (OUT/'admin.html').write_text(html,encoding='utf-8')

if __name__=='__main__':
    build('owner');build('staff');build('admin',True);build_html()
