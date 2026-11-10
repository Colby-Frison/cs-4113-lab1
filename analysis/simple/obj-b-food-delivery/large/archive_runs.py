"""Archive generated raw runs only after verifying every member's bytes."""
import hashlib
import tarfile
import shutil
from run_study import HERE, RESULTS, RUNS, SPEC
from report_study import write_csv


def digest(stream):
    h=hashlib.sha256()
    for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def archive():
    RESULTS.mkdir(parents=True,exist_ok=True)
    rows=[]
    for scenario in SPEC['scenarios']:
        for seed in SPEC['seeds']:
            root=RUNS/scenario['name']/f'seed-{seed}'
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
            rows.append({'path':str(target.relative_to(HERE)),'sha256':checksum,'files':len(members),
                         'raw_bytes':sum(m.size for m in members),'compressed_bytes':target.stat().st_size})
            print('ARCHIVED',target.relative_to(HERE),flush=True)
    write_csv(RESULTS/'archive-manifest.csv',rows)


if __name__=='__main__':archive()
