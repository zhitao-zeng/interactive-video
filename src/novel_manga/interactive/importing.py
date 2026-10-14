"""Import creative archives without changing their original content or relative paths."""
from pathlib import Path, PurePosixPath
import json
import shutil
import zipfile


def decoded_name(info: zipfile.ZipInfo) -> str:
    if not info.flag_bits & 0x800:
        try:
            return info.filename.encode('cp437').decode('utf8')
        except UnicodeError:
            pass
    return info.filename


def import_package(archive: Path, destination: Path) -> dict:
    destination.mkdir(parents=True, exist_ok=False)
    files = []
    with zipfile.ZipFile(archive) as z:
        for info in z.infolist():
            relative = PurePosixPath(decoded_name(info))
            if '__MACOSX' in relative.parts or info.is_dir():
                continue
            if relative.is_absolute() or '..' in relative.parts:
                raise ValueError(f'Archive path must stay inside the package: {relative}')
            target = destination.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as source, target.open('xb') as output:
                shutil.copyfileobj(source, output)
            files.append({'path': relative.as_posix(), 'bytes': target.stat().st_size})
    result = {'archive': archive.name, 'archive_bytes': archive.stat().st_size, 'files': files}
    (destination / 'package-index.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    return result
