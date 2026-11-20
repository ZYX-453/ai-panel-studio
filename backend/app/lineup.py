"""阵容生成：模型提议，代码裁决。

需求的原文是「AI 生成 4 位专家」。让模型自由写人设有个真问题：
它很容易生成四位"角度完全相同、只是头衔不同"的人 —— 那样讨论永远吵不起来。
所以这里的分工是：**模型负责想名字、头衔、口头禅**（这是它的强项），
**代码负责校验立场是否真的互斥、颜色是否唯一、座位是否连续**（这是可断言的）。

校验不过就重掷，最多 2 次；再不过就退回确定性模板池，绝不让一个坏阵容开演。
"""

from __future__ import annotations

import itertools
import json
import re
from typing import Any

from .consensus import _similar  # 复用同一套相似度，避免两处口径不一致
from .llm import LLMClient, LLMError, strip_code_fence

#: 6 个专属色：色相拉开且都过了浅色底的对比度，两两之间也能区分
COLORS = ("#1d4ed8", "#15803d", "#7e22ce", "#b45309", "#0e7490", "#be123c")
HOST_COLOR = "#c2410c"

#: 立场互斥门限。用与共识去重相同的度量，低于它就认为两位专家说的是同一件事。
STANCE_MIN_DISTANCE = 0.55

HOST_TEMPLATE = {
    "name": "陈立",
    "title": "圆桌主持 · 商业评论人",
    "stance": "不预设立场，负责串场、追问与收尾",
    "persona": {
        "catchphrase": "先说结论",
        "focus": ["把跑偏的话题拉回来", "追问判断的边界条件"],
        "tone": "克制、短句、少形容词",
    },
}

#: 六个互斥的切入角度。
#:
#: 立场**不拼接议题原文**：议题常常是「该不该……」「要不要……」这样的问句，
#: 硬拼进「{议题}是可运营的」会读成病句（实测就是"公司该不该给员工配……
#: 的完全访问权限是可运营的"）。所以这里写的是与议题无关的立场骨架，
#: 议题本身由界面标题承载，读者自然会把两者连起来。
#: 两两之间不会变成"同一个意思换个说法"，由 validate() 的相似度检查守住。
ANGLES: tuple[dict[str, str], ...] = (
    {
        "name": "周敏",
        "title": "组织行为学教授",
        "stance": "先别急着推进：它会削弱组织的隐性知识积累，短期收益不可持续",
        "catchphrase": "我做过一个追踪研究",
        "target": "「远程只是把工位搬回家，协作效率不受影响」",
        "focus": "弱连接、非正式交流、隐性知识",
        "tone": "学术腔，先给证据再给判断",
    },
    {
        "name": "许巍",
        "title": "SaaS 公司 CTO",
        "stance": "可以推进：问题在管理能力，不在方式本身",
        "catchphrase": "在我们那儿是这么干的",
        "target": "「协作方式换个工具就能改，不需要动管理流程」",
        "focus": "执行细节、工具链、可度量性",
        "tone": "工程腔，爱举自己团队的例子",
    },
    {
        "name": "林雪",
        "title": "HR 咨询顾问",
        "stance": "不能一刀切：应按可适用性分层，否则两头都受损",
        "catchphrase": "先分个类",
        "target": "「所有人要么适合、要么都不适合，没有中间态」",
        "focus": "分层标准、政策落地、组织公平",
        "tone": "咨询腔，喜欢给框架",
    },
    {
        "name": "何远",
        "title": "创业公司创始人",
        "stance": "看阶段：对早期团队是奢侈品，规模没上去之前谈它是本末倒置",
        "catchphrase": "活下来最重要",
        "target": "「小公司也该照搬大公司的制度，越早规范越好」",
        "focus": "资源约束、阶段差异、现金流",
        "tone": "直白、带情绪、爱打比方",
    },
    {
        "name": "程菲",
        "title": "一线运营主管",
        "stance": "看落地：决策成本不在决策者身上，所以执行时一定会变形",
        "catchphrase": "你去现场待一周就明白了",
        "target": "「制度发下去大家就会照做，执行层不会打折扣」",
        "focus": "执行成本、真实流程、一线反馈",
        "tone": "务实，常说具体场景",
    },
    {
        "name": "赵启",
        "title": "组织经济学研究者",
        "stance": "看配套：根子在考核与激励没跟着改，光喊口号没用",
        "catchphrase": "看激励结构",
        "target": "「只要方向对，人的行为自然会跟着变」",
        "focus": "激励机制、权责匹配、长期博弈",
        "tone": "冷静、爱拆因果链",
    },
)


class LineupError(ValueError):
    """阵容不合格。retry_reason 直接写进日志，便于回看是哪一条卡住的。"""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.retry_reason = reason


def build_from_pool(topic: str, expert_count: int, seed: int = 0) -> list[dict[str, Any]]:
    """确定性阵容：同 (topic, count, seed) 必得同一套人。

    重掷时 seed 变，选出的组合就变；不重掷时结果可复现，
    这样回放和"我确认的就是这一版"才对得上。
    """
    pool = list(ANGLES)
    # 用 seed 做一次稳定的循环移位，避免每次都从周敏开始
    shift = seed % len(pool)
    pool = pool[shift:] + pool[:shift]

    panel: list[dict[str, Any]] = [
        {
            "id": "p0",
            "role": "host",
            "seat": 0,
            "name": HOST_TEMPLATE["name"],
            "title": HOST_TEMPLATE["title"],
            "stance": HOST_TEMPLATE["stance"],
            "color": HOST_COLOR,
            "persona": dict(HOST_TEMPLATE["persona"]),
        }
    ]
    for i, angle in enumerate(pool[:expert_count], start=1):
        panel.append(
            {
                "id": f"p{i}",
                "role": "expert",
                "seat": i,
                "name": angle["name"],
                "title": angle["title"],
                "stance": angle["stance"],
                "color": COLORS[i - 1],
                "persona": {
                    "catchphrase": angle["catchphrase"],
                    "target": angle["target"],
                    "focus": [f.strip() for f in angle["focus"].split("、")],
                    "tone": angle["tone"],
                },
            }
        )
    return panel


def bind_to_discussion(panel: list[dict[str, Any]], did: str) -> list[dict[str, Any]]:
    """把阵容的局部 id 绑到讨论作用域上，返回新列表（不改入参）。

    `build_from_pool` 给的 id 是 "p0" / "p1"，含义是"这一场里的第几号座"；
    但 `panelist.id` 在库里是**全局主键**。于是同一套模板阵容被第二场讨论复用时，
    第二个 INSERT 直接撞 `UNIQUE constraint failed: panelist.id` —— 表现为建讨论
    接口 500，而单场测试永远发现不了（一个库只有一场时不可能撞）。

    预置样例早就按 `p_01_0` 这样带场次命名了，运行期这条路一直没跟上。
    统一在这里加前缀，两处调用点（新建 / 重掷）都必须过这一道。
    """
    return [{**p, "id": f"{did}_{p['id']}"} for p in panel]


# --------------------------------------------------------------------- 校验


def validate(panel: list[dict[str, Any]], expert_count: int) -> None:
    """结构 + 语义双重校验。任何一条不过都抛 LineupError。"""
    if len(panel) != expert_count + 1:
        raise LineupError(f"人数不符：期望 {expert_count + 1}，实际 {len(panel)}")

    hosts = [p for p in panel if p.get("role") == "host"]
    experts = [p for p in panel if p.get("role") == "expert"]
    if len(hosts) != 1:
        raise LineupError(f"主持人应恰好 1 位，实际 {len(hosts)}")
    if len(experts) != expert_count:
        raise LineupError(f"专家应 {expert_count} 位，实际 {len(experts)}")

    seats = sorted(p.get("seat") for p in panel)
    if seats != list(range(len(panel))):
        raise LineupError(f"座位必须是 0..{len(panel) - 1} 连续且唯一，实际 {seats}")

    colors = [p.get("color") for p in panel]
    if len({c for c in colors if c}) != len(colors):
        raise LineupError("存在重复颜色，专家无法按颜色区分")

    for p in panel:
        if not str(p.get("name") or "").strip():
            raise LineupError(f"座位 {p.get('seat')} 缺姓名")
        if not str(p.get("stance") or "").strip():
            raise LineupError(f"{p.get('name')} 缺立场")

    # 立场互斥：两两之间相似度不得过高
    for a, b in itertools.combinations(experts, 2):
        sim = _similar(str(a["stance"]), str(b["stance"]))
        if sim >= 1.0 - STANCE_MIN_DISTANCE:
            raise LineupError(
                f"立场过近：{a['name']} 与 {b['name']} 相似度 {sim:.2f}"
            )

    # 靶子也不能撞：两位专家想推翻同一句话，场上就成了齐射，
    # 看着像交锋，其实是同一个人说了两遍。
    # 门槛比立场更松（0.6）——靶子是整句带引号的话，公共字天然多，
    # 用立场那档 0.45 会把正常的"不同角度打同一现象"误判成重合。
    for a, b in itertools.combinations(experts, 2):
        ta = str((a.get("persona") or {}).get("target") or "")
        tb = str((b.get("persona") or {}).get("target") or "")
        if ta and tb and _similar(ta, tb) >= 0.6:
            raise LineupError(
                f"靶子重合：{a['name']} 与 {b['name']} 想推翻的是同一句话"
            )


# --------------------------------------------------------------- 模型生成

_LINEUP_SCHEMA_HINT = """
只输出 JSON，不要任何解释文字、不要 Markdown 围栏。结构：
{
  "experts": [
    {"name": "姓名", "title": "头衔", "stance": "一句话立场",
     "catchphrase": "口头禅", "target": "他最想推翻的那个说法", "tone": "说话风格"}
  ]
}
"""


def build_lineup_prompt(topic: str, expert_count: int) -> str:
    return f"""为一场圆桌讨论设计 {expert_count} 位专家。

议题：{topic}

硬性要求：
1. 每位专家必须持有一个**互不相同且相互冲突**的立场。不要出现两位只是头衔不同、观点方向相同的人。
2. 立场要具体到可以被人反驳，不要写"需要辩证看待"这类和稀泥的话。
3. 姓名用中文，头衔要真实可信（教授 / CTO / 顾问 / 创始人 / 一线主管 之类）。
4. 每人给一句口头禅，要像真人说话，不要写"我认为"这种废话。
5. target 写「他最想推翻的那个说法」：必须是一句**别人可能真的会说出口的断言**，
   用引号括起来，例如"只要方向对，人的行为自然会变"。不要写性格评价（"过于乐观"），
   不要写方法论错误（"以偏概全"）——那些是旁白，不是靶子。这一栏决定他上场后朝谁开火。
6. 各人的 target 之间也不能是同一个靶子，至少要落在不同的人或不同的做法上。
7. 不要出现真实存在的公众人物姓名。
{_LINEUP_SCHEMA_HINT}"""


def parse_model_lineup(raw: str, topic: str) -> list[dict[str, Any]]:
    """把模型输出转成 panel 结构（不含主持人，主持人固定）。"""
    text = strip_code_fence(raw)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # 兜底：从文本里抠第一个 JSON 对象，应对模型前后加了客套话
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise LineupError("模型输出不含 JSON") from None
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            raise LineupError("模型输出的 JSON 无法解析") from None

    experts = data.get("experts")
    if not isinstance(experts, list) or not experts:
        raise LineupError("模型输出缺少 experts 数组")

    panel: list[dict[str, Any]] = []
    for i, e in enumerate(experts, start=1):
        if not isinstance(e, dict):
            raise LineupError("experts 元素不是对象")
        name = str(e.get("name") or "").strip()
        title = str(e.get("title") or "").strip()
        stance = str(e.get("stance") or "").strip()
        if not (name and title and stance):
            raise LineupError(f"第 {i} 位专家缺 name/title/stance")
        panel.append(
            {
                "id": f"p{i}",
                "role": "expert",
                "seat": i,
                "name": name[:12],
                "title": title[:24],
                "stance": stance,
                "color": COLORS[(i - 1) % len(COLORS)],
                "persona": {
                    "catchphrase": str(e.get("catchphrase") or "")[:20],
                    # 模型偶尔仍按旧字段名回 attack，这里一起收；
                    # 但下游只认 target，不再有第二套写法。
                    "target": str(e.get("target") or e.get("attack") or "")[:48],
                    "focus": [s.strip() for s in str(e.get("stance") or "").replace("，", " ").split()[:3]],
                    "tone": str(e.get("tone") or "")[:30],
                },
            }
        )
    return panel


def generate(
    client: LLMClient,
    *,
    topic: str,
    expert_count: int,
    seed: int = 0,
) -> tuple[list[dict[str, Any]], str]:
    """返回 (panel, source)。source 取值 'model' / 'template'。

    模型不可用或连续不合格时退回模板池 —— 宁可阵容普通，也不能让用户
    卡在"生成失败"上，更不能拿一个立场重复的阵容开演。
    """
    host = {
        "id": "p0",
        "role": "host",
        "seat": 0,
        "name": HOST_TEMPLATE["name"],
        "title": HOST_TEMPLATE["title"],
        "stance": HOST_TEMPLATE["stance"],
        "color": HOST_COLOR,
        "persona": dict(HOST_TEMPLATE["persona"]),
    }

    last_reason = ""
    for attempt in range(3):  # 首次 + 2 次重掷
        try:
            res = client.chat(
                [{"role": "user", "content": build_lineup_prompt(topic, expert_count)}],
                temperature=0.9,
                max_tokens=1200,
                json_mode=True,
            )
            experts = parse_model_lineup(res.content, topic)
            panel = [host, *experts]
            validate(panel, expert_count)
            return panel, "model"
        except LLMError as exc:
            last_reason = f"模型调用失败: {exc}"
            # 不可重试的错误（如没配 Key）没必要再试两次
            if not exc.retryable:
                break
        except LineupError as exc:
            last_reason = exc.retry_reason

    panel = build_from_pool(topic, expert_count, seed + 1)
    validate(panel, expert_count)
    return panel, "template"


def describe_source(source: str) -> str:
    return "模型生成" if source == "model" else "预置人设池（模型不可用时的确定性回退）"
