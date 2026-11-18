"""Minimal Office Open XML fixtures, readable by Office and the importer."""
import csv
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape


def create_samples(folder):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    rows=[['校园坐标示例（米）'],['点号','东坐标','北坐标','标高'],['T01',240,100,0],['T02',240,200,'']]
    with (folder/'校园测点.csv').open('w',encoding='utf-8-sig',newline='') as stream:csv.writer(stream).writerows(rows)
    ct='http://schemas.openxmlformats.org/package/2006/content-types'
    pkg='http://schemas.openxmlformats.org/package/2006/relationships'
    rel='http://schemas.openxmlformats.org/officeDocument/2006/relationships'
    s='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    sheet='<worksheet xmlns="'+s+'"><sheetData>'
    for n,row in enumerate(rows,1):
        sheet+=f'<row r="{n}">'
        for c,value in enumerate(row):
            sheet+=f'<c r="{chr(65+c)}{n}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>'
        sheet+='</row>'
    sheet+='</sheetData></worksheet>'
    with zipfile.ZipFile(folder/'校园测点.xlsx','w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml',f'<Types xmlns="{ct}"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
        z.writestr('_rels/.rels',f'<Relationships xmlns="{pkg}"><Relationship Id="rId1" Type="{rel}/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr('xl/workbook.xml',f'<workbook xmlns="{s}" xmlns:r="{rel}"><sheets><sheet name="测点表" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels',f'<Relationships xmlns="{pkg}"><Relationship Id="rId1" Type="{rel}/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr('xl/worksheets/sheet1.xml',sheet)
    w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'
    table='<w:tbl>'+''.join('<w:tr>'+''.join('<w:tc><w:p><w:r><w:t>'+escape(str(v))+'</w:t></w:r></w:p></w:tc>' for v in row)+'</w:tr>' for row in rows)+'</w:tbl>'
    with zipfile.ZipFile(folder/'校园测点.docx','w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml',f'<Types xmlns="{ct}"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr('_rels/.rels',f'<Relationships xmlns="{pkg}"><Relationship Id="rId1" Type="{rel}/officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr('word/document.xml',f'<w:document xmlns:w="{w}"><w:body>{table}<w:sectPr/></w:body></w:document>')
    return folder
