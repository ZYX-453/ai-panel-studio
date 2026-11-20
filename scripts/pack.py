"""打交付 zip：只装源码 + 文档 + 截图，不装依赖和数据库。

为什么要单独写一个脚本：交付包和仓库的"该包含什么"不是一回事。
仓库要能被 `git clone` 后跑起来，所以不含 node_modules / .venv；
但交付包是给评审直接解压看的，必须能一眼找到文档和截图，
又不能夹带 82M 的依赖目录。两条口径分开写，才不会互相污染。

用法：
    python scripts/pack.py                 # 输出到 ../交付/
    python scripts/pack.py D:/somewhere    # 指定输出目录
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 目录级排除：依赖、缓存、运行时产物
EXCLUDE_DIRS = {
    ".git", ".venv", "venv", "node_modules", "dist", "__pycache__",
    ".pytest_cache", ".vite", "shots", "_shots", ".tmp", "_tmp",
}

# 文件级排除：数据库、日志、临时文件、密钥
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".log", ".db", ".db-wal", ".db-shm", ".tmp"}
EXCLUDE_NAMES = {".DS_Store", "Thumbs.db", ".env"}


def wanted(path: Path) -> bool:
    if any(part in EXCLUDE_DIRS for part in path.parts):
        return False
    if path.name in EXCLUDE_NAMES or path.name.startswith(".env."):
        return False
    if path.name.startswith(".tmp"):
        return False
    return not (path.suffix in EXCLUDE_SUFFIXES)


def main() -> int:
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT.parent / "交付"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "AI圆桌讨论-张宇翔-20230308043.zip"

    files = sorted(p for p in ROOT.rglob("*") if p.is_file() and wanted(p.relative_to(ROOT)))

    # 数据库初始化脚本和样例数据是交付物硬性要求，缺了直接报错，
    # 不要静悄悄打出一个"看起来完整"的包。
    must_have = [
        "README.md",
        "scripts/init_db.py",
        "db/schema.sql",
        "db/seed.sql",
        "docs/09-工作流说明.md",
        "docs/07-核心Prompt记录.md",
    ]
    names = {str(p.relative_to(ROOT)).replace("\\", "/") for p in files}
    missing = [m for m in must_have if m not in names]
    if missing:
        print(f"[FAIL] 交付包缺少必需文件：{missing}")
        return 1

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for p in files:
            zf.write(p, Path("AI圆桌讨论") / p.relative_to(ROOT))

    size = out.stat().st_size / 1024 / 1024
    print(f"[OK] {len(files)} 个文件 → {out}")
    print(f"     压缩后 {size:.2f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
