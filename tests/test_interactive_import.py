from pathlib import Path
import zipfile
import pytest

from novel_manga.interactive.importing import decoded_name, import_package


def test_legacy_chinese_name():
    info = zipfile.ZipInfo('角色资产/林野.png'.encode('utf8').decode('cp437'))
    assert decoded_name(info) == '角色资产/林野.png'


def test_import_keeps_relative_references_and_never_overwrites(tmp_path):
    archive = tmp_path / 'package.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('package/routes/shared/assets.json', '{"path":"../../images/林野.png"}')
        z.writestr('package/images/林野.png', b'reference')
        z.writestr('__MACOSX/._junk', b'junk')
    target = tmp_path / 'imported'
    result = import_package(archive, target)
    assert len(result['files']) == 2
    assert (target / 'package/routes/shared/../../images/林野.png').read_bytes() == b'reference'
    (target / 'package/images/林野.png').write_bytes(b'user-work')
    with pytest.raises(FileExistsError):
        import_package(archive, target)
    assert (target / 'package/images/林野.png').read_bytes() == b'user-work'
