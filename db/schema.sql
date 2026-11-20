-- =============================================================================
--  AI Panel Studio · SQLite DDL
--  阶段：SDD-4 ｜ 对应文档：docs/02-SCHEMA.md
--
--  设计要点（与文档一一对应，改动须同步两边）：
--   1. 一切以 discussion_id 为隔离边界，多场讨论并行不串场
--   2. 「举手 / 准备发言」等内部事件只存在于 event 表，
--      utterance 表结构上就没有这类字段 —— 靠 schema 保证，不靠前端纪律
--   3. event 表是唯一重放源，(discussion_id, seq) 唯一，保证可补发、可去重
--   4. consensus_state 以 discussion_id 为主键，天然保证「一场只有一个当前版本」
-- =============================================================================

PRAGMA foreign_keys = ON;

-- -----------------------------------------------------------------------------
-- 讨论场次
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS discussion (
    id            TEXT PRIMARY KEY,
    topic         TEXT    NOT NULL CHECK (length(trim(topic)) BETWEEN 2 AND 200),
    expert_count  INTEGER NOT NULL CHECK (expert_count BETWEEN 2 AND 6),
    status        TEXT    NOT NULL CHECK (status IN (
                              'GENERATING',        -- 正在生成阵容
                              'AWAITING_CONFIRM',  -- 阵容已出，等用户确认（此阶段不烧模型）
                              'LIVE',              -- 讨论进行中
                              'FINISHED',          -- 正常或降级结束
                              'FAILED'             -- 生成/启动失败
                          )),
    ended_reason  TEXT    CHECK (ended_reason IS NULL OR ended_reason IN (
                              'converged',   -- 达成收敛
                              'max_turns',   -- 触达硬上限
                              'degraded'     -- 模型连续失败，降级收尾
                          )),
    turn_count    INTEGER NOT NULL DEFAULT 0 CHECK (turn_count >= 0),
    max_turns     INTEGER NOT NULL DEFAULT 24 CHECK (max_turns BETWEEN 6 AND 60),
    config_json   TEXT    NOT NULL DEFAULT '{}',  -- K 值、调度权重等可调参数
    final_summary TEXT,                            -- 主持人收尾（仅注入 consensus_state 生成）
    created_at    TEXT    NOT NULL,
    started_at    TEXT,
    ended_at      TEXT
);

-- 状态与结束原因必须同时成立，避免出现「FINISHED 但没写为什么结束」
CREATE TRIGGER IF NOT EXISTS trg_discussion_ended_reason
BEFORE UPDATE OF status ON discussion
WHEN NEW.status = 'FINISHED' AND NEW.ended_reason IS NULL
BEGIN
    SELECT RAISE(ABORT, 'FINISHED 必须写 ended_reason');
END;

-- -----------------------------------------------------------------------------
-- 嘉宾阵容（主持人 + 专家）
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS panelist (
    id            TEXT PRIMARY KEY,
    discussion_id TEXT    NOT NULL REFERENCES discussion(id) ON DELETE CASCADE,
    role          TEXT    NOT NULL CHECK (role IN ('host', 'expert')),
    seat          INTEGER NOT NULL CHECK (seat >= 0),
    name          TEXT    NOT NULL CHECK (length(trim(name)) > 0),
    title         TEXT    NOT NULL,   -- 职业 / Title
    stance        TEXT    NOT NULL,   -- 一句话立场，须与同场其他人互斥
    color_token   TEXT    NOT NULL,   -- 专属颜色（hex），前端据此渲染
    persona_json  TEXT    NOT NULL DEFAULT '{}',  -- 口头禅 / 攻击面 / 关注点
    UNIQUE (discussion_id, seat)
);

-- -----------------------------------------------------------------------------
-- 发言（transcript 的唯一来源）
-- 注意：这里没有 raise_hand / is_speaking 之类的字段，是刻意的
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS utterance (
    id            TEXT PRIMARY KEY,
    discussion_id TEXT    NOT NULL REFERENCES discussion(id) ON DELETE CASCADE,
    panelist_id   TEXT    NOT NULL REFERENCES panelist(id)  ON DELETE CASCADE,
    seq           INTEGER NOT NULL CHECK (seq > 0),
    phase         TEXT    NOT NULL CHECK (phase IN ('OPENING', 'EXPLORE', 'CLASH', 'CLOSING')),
    intent        TEXT    NOT NULL,   -- 补充 / 反驳 / 追问 / 抛论点
    content       TEXT    NOT NULL CHECK (length(trim(content)) > 0),
    reply_to_id   TEXT    REFERENCES utterance(id) ON DELETE SET NULL,  -- 在回应哪一句
    created_at    TEXT    NOT NULL,
    UNIQUE (discussion_id, seq)
);

-- -----------------------------------------------------------------------------
-- 共识 / 分歧：唯一真相源。页面与最终总结都从这里取，不各自生成
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS consensus_state (
    discussion_id TEXT    PRIMARY KEY REFERENCES discussion(id) ON DELETE CASCADE,
    version       INTEGER NOT NULL DEFAULT 0 CHECK (version >= 0),
    points_json   TEXT    NOT NULL DEFAULT '[]',   -- 已达成的共识
    disputes_json TEXT    NOT NULL DEFAULT '[]',   -- 仍存在的分歧
    based_on_seq  INTEGER NOT NULL DEFAULT 0,      -- 这一版基于前多少条发言
    updated_at    TEXT    NOT NULL
);

-- 历史版本（页面显示 v3 那种编号的来源，也是"实时在更新"的证据）
CREATE TABLE IF NOT EXISTS consensus_snapshot (
    id            TEXT PRIMARY KEY,
    discussion_id TEXT    NOT NULL REFERENCES discussion(id) ON DELETE CASCADE,
    version       INTEGER NOT NULL CHECK (version > 0),
    points_json   TEXT    NOT NULL,
    disputes_json TEXT    NOT NULL,
    based_on_seq  INTEGER NOT NULL,
    created_at    TEXT    NOT NULL,
    UNIQUE (discussion_id, version)
);

-- -----------------------------------------------------------------------------
-- 事件流：直播推送 + 断线重放 + 结束后回看，共用这一张表
-- panelist_state 是「准备发言 / 发言中」，属于内部事件，不进 transcript
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS event (
    row_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    discussion_id TEXT    NOT NULL REFERENCES discussion(id) ON DELETE CASCADE,
    seq           INTEGER NOT NULL CHECK (seq >= 0),
    type          TEXT    NOT NULL CHECK (type IN (
                              'phase',           -- 阶段切换
                              'panelist_state',  -- 待机 / 准备发言 / 发言中（仅侧边小窗）
                              'utterance',       -- 一条发言完成（仅 transcript）
                              'consensus',       -- 共识版本更新
                              'ended',           -- 讨论结束
                              'error'            -- 单次调用失败但可继续
                          )),
    payload_json  TEXT    NOT NULL,
    created_at    TEXT    NOT NULL,
    UNIQUE (discussion_id, seq)
);

-- -----------------------------------------------------------------------------
-- 索引
-- -----------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_utterance_discussion_seq ON utterance(discussion_id, seq);
CREATE INDEX IF NOT EXISTS idx_event_discussion_seq      ON event(discussion_id, seq);
CREATE INDEX IF NOT EXISTS idx_panelist_discussion        ON panelist(discussion_id, seat);
CREATE INDEX IF NOT EXISTS idx_discussion_status          ON discussion(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_snapshot_discussion        ON consensus_snapshot(discussion_id, version);

-- -----------------------------------------------------------------------------
-- 视图：首页列表。进行中优先，其次最近结束的
-- -----------------------------------------------------------------------------
CREATE VIEW IF NOT EXISTS v_discussion_list AS
SELECT
    d.id,
    d.topic,
    d.status,
    d.ended_reason,
    d.turn_count,
    d.created_at,
    d.ended_at,
    (SELECT COUNT(*) FROM panelist p
      WHERE p.discussion_id = d.id AND p.role = 'expert')      AS expert_count,
    (SELECT COUNT(*) FROM utterance u
      WHERE u.discussion_id = d.id)                            AS utterance_count,
    (SELECT version FROM consensus_state c
      WHERE c.discussion_id = d.id)                            AS consensus_version
FROM discussion d;
