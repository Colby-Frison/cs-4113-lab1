"""Read saved runs and archive raw evidence after verifying its contents."""
from contextlib import contextmanager
import csv
import hashlib
from pathlib import Path
import shutil
import tarfile
import tempfile


@contextmanager
def saved_run(runs,scenario,seed):
    root=runs/scenario/f'seed-{seed}'
    if root.is_dir():
        yield root
    else:
        archive=root.with_suffix('.tar.gz')
        with tempfile.TemporaryDirectory(prefix='food-study-report-') as temp:
            with tarfile.open(archive) as tar:tar.extractall(temp,filter='data')
            yield Path(temp)/root.name


def write_csv(path,rows):
    with path.open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)


def digest(stream):
    h=hashlib.sha256()
    for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def archive(here,results,runs,spec):
    results.mkdir(parents=True,exist_ok=True)
    rows=[]
    for scenario in spec['scenarios']:
        for seed in spec['seeds']:
            root=runs/scenario['name']/f'seed-{seed}'
            target=root.with_suffix('.tar.gz')
            if root.is_dir():
                expected={}
                for path in root.rglob('*'):
                    if path.is_file():
                        with path.open('rb') as f:expected[str(path.relative_to(root.parent))]=digest(f)
                if not target.exists():
                    temporary=target.with_suffix('.tmp')
                    with tarfile.open(temporary,'w:gz',compresslevel=3) as tar:tar.add(root,arcname=root.name)
                    temporary.rename(target)
                with tarfile.open(target) as tar:
                    actual={member.name:digest(tar.extractfile(member)) for member in tar if member.isfile()}
                if actual!=expected:raise RuntimeError(f'archive differs from source: {target}')
                shutil.rmtree(root)
            with tarfile.open(target) as tar:
                members=[member for member in tar if member.isfile()]
                if not any(m.name.endswith('/analysis/summary.json') for m in members):raise RuntimeError('missing summary')
            with target.open('rb') as f:checksum=digest(f)
            rows.append({'path':str(target.relative_to(here)),'sha256':checksum,'files':len(members),
                         'raw_bytes':sum(m.size for m in members),'compressed_bytes':target.stat().st_size})
            print('ARCHIVED',target.relative_to(here),flush=True)
    write_csv(results/'archive-manifest.csv',rows)
