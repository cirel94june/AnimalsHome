"""把从 R2 下载的备份解密解包到一个新目录（真要恢复时用）。不会碰已有的 data/。

    python tools/offsite_restore.py 备份文件.tar.gz.enc 解到哪个新目录

备份密码在运行时输入，不写在命令里。依赖 cryptography。
"""

from __future__ import annotations

import getpass
import sys
import tarfile
import tempfile
from pathlib import Path


def main() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from offsite_backup import check_restored, decrypt_stream

    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    source, target = Path(sys.argv[1]), Path(sys.argv[2])
    if target.exists() and any(target.iterdir()):
        print("目标目录不是空的，换一个新目录")
        return 2
    passphrase = getpass.getpass("备份密码：")
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / "b.tar.gz"
        with open(source, "rb") as sealed, open(archive, "wb") as out:
            decrypt_stream(sealed, out, passphrase)
        with tarfile.open(archive, "r:gz") as tar:
            tar.extractall(target, filter="data")
    print(check_restored(target))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
