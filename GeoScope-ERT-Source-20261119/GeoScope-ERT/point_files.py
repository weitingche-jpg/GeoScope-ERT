"""Offline table extraction; no document macros or formulas are executed."""
import csv
import io
import math
import re
import zipfile
import itertools
from pathlib import Path
from xml.etree import ElementTree as ET

ALIASES={
    'id':{'id','name','station','stationid','pointid','点号','测点','测点号','测点编号','编号','测点名称'},
    'x':{'x','e','east','easting','东坐标','东向坐标','x坐标'},
    'y':{'y','n','north','northing','北坐标','北向坐标','y坐标'},
    'z':{'z','h','elevation','height','高程','标高','z坐标'}}


def normalized(value):
    return re.sub(r'\s|[（(].*?[）)]','',str(value or '')).lower()


def xml_part(archive,name):
    data=archive.read(name)
    for strict,standard in [
        (b'http://purl.oclc.org/ooxml/spreadsheetml/main',b'http://schemas.openxmlformats.org/spreadsheetml/2006/main'),
        (b'http://purl.oclc.org/ooxml/wordprocessingml/main',b'http://schemas.openxmlformats.org/wordprocessingml/2006/main'),
        (b'http://purl.oclc.org/ooxml/officeDocument/relationships',b'http://schemas.openxmlformats.org/officeDocument/2006/relationships')]:
        data=data.replace(strict,standard)
    return ET.fromstring(data)


def infer_header(rows):
    best=(0,-1,{})
    for index,row in enumerate(rows[:30]):
        mapping={}
        for col,value in enumerate(row):
            for field,aliases in ALIASES.items():
                if normalized(value) in aliases and field not in mapping:
                    mapping[field]=col
        score=len(mapping)+4*int('x' in mapping and 'y' in mapping)
        if score>best[1]:best=(index,score,mapping)
    return best[0],best[2]


def read_tables(path):
    path=Path(path); suffix=path.suffix.lower()
    if path.stat().st_size>20*1024*1024:raise ValueError('文件超过20MB，请提取测点表后重试。')
    if suffix in ('.xls','.doc'):
        raise ValueError('请先用 Office/WPS 将旧文件另存为 .xlsx 或 .docx。')
    if suffix=='.csv':
        raw=path.read_bytes()
        if raw.startswith((b'\xff\xfe',b'\xfe\xff')):text=raw.decode('utf-16')
        else:
            try:text=raw.decode('utf-8-sig')
            except UnicodeDecodeError:text=raw.decode('gb18030')
        try:dialect=csv.Sniffer().sniff(text[:10000],delimiters=',;\t')
        except csv.Error:dialect=csv.excel
        rows=list(itertools.islice(csv.reader(io.StringIO(text),dialect),10001))
        if len(rows)>10000:raise ValueError('最多导入10000行，请拆分文件。')
        return [(path.name,rows)]
    if suffix not in ('.xlsx','.docx'):
        raise ValueError('支持 .xlsx、.docx 和 .csv 文件。')
    with zipfile.ZipFile(path) as archive:
        if sum(i.file_size for i in archive.infolist())>80*1024*1024:
            raise ValueError('文档解压后超过80MB，请提取测点表后重试。')
        if suffix=='.docx':
            ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
            doc=xml_part(archive,'word/document.xml')
            tables=[]
            for n,table in enumerate(doc.findall('./w:body/w:tbl',ns),1):
                rows=[['\n'.join(
                    ''.join(t.text or '' for t in p.findall('.//w:t',ns))
                    for p in cell.findall('w:p',ns)) for cell in row.findall('w:tc',ns)]
                    for row in table.findall('w:tr',ns)]
                if len(rows)>10000:raise ValueError('最多导入10000行，请拆分文件。')
                tables.append((f'Word 表格 {n}',rows))
            if not tables:raise ValueError('Word 中未找到可读取表格；图片/扫描件暂不支持。')
            return tables
        ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        strings=[]
        if 'xl/sharedStrings.xml' in archive.namelist():
            strings=[''.join(t.text or '' for t in si.findall('.//s:t',ns))
                for si in xml_part(archive,'xl/sharedStrings.xml').findall('s:si',ns)]
        rels=xml_part(archive,'xl/_rels/workbook.xml.rels')
        targets={r.attrib['Id']:r.attrib['Target'] for r in rels}
        workbook=xml_part(archive,'xl/workbook.xml');tables=[]
        for sheet in workbook.findall('s:sheets/s:sheet',ns):
            rid=sheet.attrib['{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id']
            target=targets[rid];target=target.lstrip('/') if target.startswith('/') else 'xl/'+target
            tree=xml_part(archive,target);rows=[];last=0
            for row in tree.findall('s:sheetData/s:row',ns):
                number=int(row.attrib.get('r',last+1))
                if number>10000:raise ValueError('工作表行数超过10000，请裁剪测点表。')
                rows.extend([[] for _ in range(number-last-1)]);values=[]
                for cell in row.findall('s:c',ns):
                    letters=re.match(r'[A-Z]+',cell.attrib.get('r','A')).group()
                    col=0
                    for letter in letters:col=col*26+ord(letter)-64
                    if col>1024:raise ValueError('表格列数过多，请裁剪测点区域。')
                    values.extend(['']*max(0,col-len(values)))
                    typ=cell.attrib.get('t');v=cell.find('s:v',ns);value=v.text if v is not None else ''
                    if typ=='s':value=strings[int(value)] if value else ''
                    if typ=='inlineStr':value=''.join(t.text or '' for t in cell.findall('.//s:t',ns))
                    if cell.find('s:f',ns) is not None and not value:value='#公式无缓存值'
                    values[col-1]=value or ''
                rows.append(values);last=number
            tables.append((sheet.attrib['name'],rows))
        return tables


def convert_rows(rows,mapping,scale,height,inside,excluded=()):
    result=[];seen=set()
    for index,row in enumerate(rows):
        def value(field):
            col=mapping.get(field,-1)
            return str(row[col]).strip() if 0<=col<len(row) and row[col] is not None else ''
        if not any(str(v if v is not None else '').strip() for v in row):continue
        record=dict(index=index,raw=list(row),point=None,error='',excluded=index in excluded)
        try:
            if not {'x','y'}<=mapping.keys():raise ValueError('请指定 X 和 Y 列')
            name=value('id') or f'P{index+1:03}'
            x,y=float(value('x'))*scale,float(value('y'))*scale
            if not math.isfinite(x) or not math.isfinite(y):raise ValueError('坐标必须为有限数值')
            if not inside(x,y):raise ValueError('测点超出有效地面范围')
            ztext=value('z');z=float(ztext)*scale if ztext else float(height([[x,y]]))
            if not math.isfinite(z):raise ValueError('高程不是有效数值')
            if name in seen and index not in excluded:raise ValueError('文件内编号重复')
            if index not in excluded:seen.add(name)
            record['point']=dict(id=name,x=x,y=y,z=z,elevation_source='实测/文件' if ztext else '地形插值')
        except (ValueError,TypeError,OverflowError) as error:
            record['error']=str(error)
        result.append(record)
    return result
