"""打交付 zip：只装 git 追踪的文件。

为什么不自己维护排除清单：第一版是按后缀猜的，结果把 8 个
`vite.config.ts.timestamp-*.mjs` 打进了包 —— `.gitignore` 早就声明它们是垃圾，
可脚本有自己的规则，两套口径必然打架。现在改成直接问 git 要文件清单：
交付包内容 == 评审 clone 下来的内容，这才是应该相等的东西。

另一个理由是语义：这份作业的主交付物是仓库链接，zip 只是附件。
两者内容不一致的话，附件就成了"另一份东西"，解释起来都是麻烦。

用法：
    python scripts/pack.py                 # 输出到 ../交付/
    python scripts/pack.py D:/somewhere    # 指定输出目录
"""
from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = "AI圆桌讨论-张宇翔-20230308060"

# 数据库初始化脚本和样例数据是交付物硬性要求，缺了直接报错，
# 不要静悄悄打出一个"看起来完整"的包。
MUST_HAVE = [
    "README.md",
    "scripts/init_db.py",
    "db/schema.sql",
    "db/seed.sql",
    "docs/06-开发说明.md",
    "docs/07-核心Prompt记录.md",
    "docs/08-已完成与未完成.md",
    "docs/09-工作流说明.md",
    "docs/screenshots/03-live.png",
]


def tracked_files() -> list[Path]:
    """git 追踪的文件；不是 git 仓库时退回文件系统遍历。"""
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=ROOT, capture_output=True, check=True,
        ).stdout
        rels = [p for p in out.decode("utf-8").split("\0") if p]
        return sorted(ROOT / r for r in rels if (ROOT / r).is_file())
    except (subprocess.CalledProcessError, FileNotFoundError):
        skip = {".git", "node_modules", ".venv", "__pycache__", "dist", "data"}
        return sorted(
            p for p in ROOT.rglob("*")
            if p.is_file() and not (skip & set(p.relative_to(ROOT).parts))
        )


def main() -> int:
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT.parent / "交付"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{NAME}.zip"

    files = tracked_files()
    names = {p.relative_to(ROOT).as_posix() for p in files}
    missing = [m for m in MUST_HAVE if m not in names]
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
