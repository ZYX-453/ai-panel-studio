"""后端入口。

    uvicorn main:app --reload --port 8000

也可以直接 `python main.py`（带默认端口），方便不熟悉 uvicorn 的人跑起来。
"""

from __future__ import annotations

import os

from app.api import app

__all__ = ["app"]


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=int(os.environ.get("PANEL_PORT", "8000")),
        reload=False,
    )
