"""Check document references and detect source drift with a reviewed manifest.

--write-manifest is for maintainers AFTER updating/reviewing the guide. Hashes
detect changes, but cannot prove the prose, diagrams or hardware are correct.
Text hashes normalize CRLF to LF so Git checkout settings do not cause drift.
"""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT/'docs/TABLETOP_OS_TECHNICAL_GUIDE_ZH.md'
MANIFEST = ROOT/'docs/CODE_REFERENCE_MANIFEST.json'


def source_hashes():
    names = subprocess.check_output(['git','ls-files','--cached','--others','--exclude-standard','-z'],cwd=ROOT).decode('utf-8').split('\0')
    prefixes = ('pc-runtime/','pi-bridge/','firmware/','protocol/','tools/')
    suffixes = {'.py','.ps1','.cpp','.hpp','.h','.ino','.json','.toml','.sh','.service'}
    return {name:hashlib.sha256((ROOT/name).read_bytes().replace(b'\r\n', b'\n')).hexdigest() for name in sorted(set(names))
            if name.startswith(prefixes) and (Path(name).suffix in suffixes or Path(name).name=='CMakeLists.txt')}


def python_symbols(source):
    result=set()
    def visit(node, prefix=''):
        for child in ast.iter_child_nodes(node):
            if isinstance(child,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):
                name=prefix+child.name
                result.add(name)
                visit(child,name+'.')
            else:
                visit(child,prefix)
    visit(ast.parse(source))
    return result


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--write-manifest',action='store_true')
    args=parser.parse_args()
    errors=[]
    guide=GUIDE.read_text(encoding='utf-8')
    current=source_hashes()
    if args.write_manifest:
        MANIFEST.write_text(json.dumps({'v':1,'files':current},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    if not MANIFEST.exists():
        errors.append('Missing source manifest')
    else:
        saved=json.loads(MANIFEST.read_text(encoding='utf-8'))['files']
        for name in sorted(saved.keys()|current.keys()):
            if saved.get(name)!=current.get(name):
                errors.append('Source changed; review guide then update manifest: '+name)
    files=[GUIDE,ROOT/'README.md',ROOT/'pc-runtime/README.md',ROOT/'pi-bridge/README.md',
           ROOT/'protocol/README.md',ROOT/'docs/HARDWARE_COMMISSIONING_ZH.md',ROOT/'docs/VALIDATION_2026-10-16.md']
    links=0
    for document in files:
        source=document.read_text(encoding='utf-8')
        for link in re.findall(r'\[[^\]]*\]\(([^)]+)\)',source):
            if link.startswith(('https://','http://','#')):
                continue
            links+=1
            if not (document.parent/link.split('#',1)[0]).is_file():
                errors.append(f'Broken link in {document.name}: {link}')
    references=re.findall(r'<!-- code-ref: ([^\s]+) -->',guide)
    for reference in references:
        name,_,symbol=reference.partition('::')
        path=ROOT/name
        if not path.is_file():
            errors.append('Missing code reference: '+reference)
            continue
        source=path.read_text(encoding='utf-8')
        found=symbol in python_symbols(source) if path.suffix=='.py' else (
            bool(re.search(r'\b'+re.escape(symbol)+r'\s*\(',source)))
        if not found:
            errors.append('Missing symbol: '+reference)
    sections=re.findall(r'^## ([A-K])\.',guide,re.M)
    if sections!=list('ABCDEFGHIJK'):
        errors.append('Guide must contain sections A-K in order')
    fences=re.findall(r'^```([^\n]*)\n(.*?)^```\s*$',guide,re.M|re.S)
    if guide.count('```')!=2*len(fences):
        errors.append('Unbalanced code fences')
    for language,body in fences:
        if language=='json':
            try:
                decoder=json.JSONDecoder()
                while body.strip():
                    _,end=decoder.raw_decode(body.lstrip())
                    body=body.lstrip()[end:]
            except ValueError as exc:
                errors.append('Invalid JSON example: '+str(exc))
    firmware=(ROOT/'firmware/arduino/TabletopOS_Lower_Controller/TabletopOS_Lower_Controller.ino').read_text(encoding='utf-8')
    for language,body in fences:
        if language=='cpp' and body.startswith('void loop()') and body.strip() not in firmware:
            errors.append('Firmware loop excerpt changed')
    report={'source_files':len(current),'local_links':links,'explicit_code_references':len(references),
            'mermaid_blocks':sum(language=='mermaid' for language,_ in fences),'errors':errors}
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return bool(errors)


if __name__=='__main__':
    raise SystemExit(main())
