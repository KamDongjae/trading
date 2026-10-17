# -*- coding: utf-8 -*-
"""reports/SETTINGS.md -> reports/SETTINGS.pdf
사용: python build_settings_pdf.py [md경로] [pdf경로]
필요: pip install markdown playwright (+ playwright install chromium 또는 시스템 크롬)
한글 폰트는 시스템 폰트(Noto Sans CJK / 맑은 고딕)를 씀."""
import sys, os, markdown

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
md_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(BASE, 'reports', 'SETTINGS.md')
pdf_path = sys.argv[2] if len(sys.argv) > 2 else os.path.join(BASE, 'reports', 'SETTINGS.pdf')

CSS = """
@page { size: A4; margin: 14mm 12mm; }
body { font-family: 'Noto Sans CJK KR','Malgun Gothic','Apple SD Gothic Neo',sans-serif; font-size: 10pt; color:#222; line-height:1.45; }
h1 { font-size: 18pt; border-bottom: 2px solid #1a5fb4; padding-bottom: 4px; margin: 0 0 6px; }
h2 { font-size: 13pt; color:#1a5fb4; margin: 16px 0 6px; }
table { border-collapse: collapse; width: 100%; margin: 6px 0 10px; font-size: 9pt; page-break-inside: auto; }
th, td { border: 1px solid #c8ccd2; padding: 4px 6px; vertical-align: top; }
th { background: #eef2f8; }
tr { page-break-inside: avoid; }
code { font-family: Consolas, 'DejaVu Sans Mono', monospace; background:#f3f4f6; padding:0 3px; border-radius:3px; font-size:8.8pt; }
p, li { margin: 3px 0; }
"""

def main():
    with open(md_path, encoding='utf-8') as f:
        html_body = markdown.markdown(f.read(), extensions=['tables'])
    html = f"<!doctype html><html><head><meta charset='utf-8'><style>{CSS}</style></head><body>{html_body}</body></html>"
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        kw = {}
        exe = os.environ.get('CHROME_PATH')
        if exe:
            kw['executable_path'] = exe
        b = p.chromium.launch(**kw)
        pg = b.new_page()
        pg.set_content(html, wait_until='load')
        pg.pdf(path=pdf_path, format='A4', print_background=True,
               margin={'top': '14mm', 'bottom': '14mm', 'left': '12mm', 'right': '12mm'})
        b.close()
    print('saved', pdf_path)

if __name__ == '__main__':
    main()
