import type {
  Consensus,
  DiscussionDetail,
  DiscussionSummary,
  Panelist,
  StudioEvent,
  Utterance,
} from '../lib/types'

/**
 * 设计期假数据（DDD 阶段专用）。
 *
 * 内容与 db/seed.sql 的 d_01 / d_03 保持一致 —— 后面的 E2E 断言可以直接
 * 拿这里的数字和数据库对账，不会出现"设计稿和真数据是两套东西"。
 *
 * 注意：这里没有任何定时器驱动的"伪直播"。假数据只用来**手动步进**看界面，
 * 真实推进只能由后端 SSE 事件驱动，见 projectEvents()。
 */

const panel01: Panelist[] = [
  { id: 'p_01_0', role: 'host', seat: 0, name: '陈立', title: '圆桌主持 · 商业评论人', stance: '不预设立场，负责串场、追问与收尾', color: '#c2410c' },
  { id: 'p_01_1', role: 'expert', seat: 1, name: '周敏', title: '组织行为学教授', stance: '远程会削弱弱连接，长期伤害创新能力', color: '#1d4ed8' },
  { id: 'p_01_2', role: 'expert', seat: 2, name: '许巍', title: 'SaaS 公司 CTO', stance: '远程是可运营的，问题在管理能力不在物理距离', color: '#15803d' },
  { id: 'p_01_3', role: 'expert', seat: 3, name: '林雪', title: 'HR 咨询顾问', stance: '一刀切都错，应按岗位可远程性分层', color: '#7e22ce' },
  { id: 'p_01_4', role: 'expert', seat: 4, name: '何远', title: '创业公司创始人', stance: '早期公司必须线下，规模上去之后才有得选', color: '#b45309' },
]

const transcript01: Utterance[] = [
  { id: 'u1', panelistId: 'p_01_0', seq: 1, phase: 'OPENING', intent: '抛论点', content: '今天聊远程办公。先立个规矩：不谈个人偏好，只谈证据。周敏，你先说，远程对组织到底损伤了什么？', replyToSeq: null, createdAt: '09:13:05' },
  { id: 'u2', panelistId: 'p_01_1', seq: 2, phase: 'EXPLORE', intent: '抛论点', content: '弱连接。我做过一个三年的追踪研究，远程之后跨部门的非正式交流降了将近一半，而创新恰恰来自这些看起来没什么用的闲聊。', replyToSeq: 1, createdAt: '09:13:32' },
  { id: 'u3', panelistId: 'p_01_2', seq: 3, phase: 'EXPLORE', intent: '反驳', content: '我不同意弱连接这个说法。在我们那儿远程两年，跨部门连接反而变多了，因为不用约会议室、不用等谁有空。', replyToSeq: 2, createdAt: '09:14:01' },
  { id: 'u4', panelistId: 'p_01_4', seq: 4, phase: 'EXPLORE', intent: '补充', content: '你们说的都是已经跑起来的公司。活下来最重要的时候，我们二十个人靠的是每天坐在一起吵出来的默契，远程长不出这个东西。', replyToSeq: 3, createdAt: '09:14:28' },
  { id: 'u5', panelistId: 'p_01_3', seq: 5, phase: 'EXPLORE', intent: '抛论点', content: '先分个类。岗位的可远程性差了三倍以上，把客服和算法工程师放进同一个政策里讨论，注定得不出结论。', replyToSeq: 4, createdAt: '09:14:55' },
  { id: 'u6', panelistId: 'p_01_2', seq: 6, phase: 'CLASH', intent: '反驳', content: '而且弱连接这个指标本身可疑。非正式交流变少了，但书面沟通变多了，写下来的东西反而能留存。', replyToSeq: 2, createdAt: '09:15:40' },
  { id: 'u7', panelistId: 'p_01_1', seq: 7, phase: 'CLASH', intent: '反驳', content: '留存不等于流通。文档能查到的东西，不会在你还没意识到需要它的时候撞到你脸上，这是两件不同的事。', replyToSeq: 6, createdAt: '09:16:12' },
  { id: 'u8', panelistId: 'p_01_4', seq: 8, phase: 'CLASH', intent: '补充', content: '这句我同意周敏。我们远程之后最明显的损失就是，没人会在茶水间顺口告诉你某个客户上周抱怨了什么。', replyToSeq: 7, createdAt: '09:16:44' },
  { id: 'u9', panelistId: 'p_01_3', seq: 9, phase: 'CLASH', intent: '追问', content: '何远你说的是阶段问题。二十人靠默契，两百人靠流程，把早期经验当成普遍规律会误导决策。', replyToSeq: 4, createdAt: '09:17:15' },
  { id: 'u10', panelistId: 'p_01_1', seq: 10, phase: 'CLASH', intent: '补充', content: '分层我接受，但补一句：分层的前提是先量过。多数公司连自己哪些岗位真的可远程都没测过，就直接分层了。', replyToSeq: 9, createdAt: '09:17:50' },
  { id: 'u11', panelistId: 'p_01_0', seq: 11, phase: 'CLOSING', intent: '总结', content: '收一下。今天没分出胜负，但有三件事清楚了：弱连接的损失是真的、书面沟通的替代作用也是真的、分层要先量再分。还差的，是同一家公司在两个阶段的前后对照数据。', replyToSeq: 10, createdAt: '09:19:40' },
]

const consensus01: Consensus = {
  version: 3,
  points: [
    '远程对弱连接的损伤有实证支持，不是主观感受',
    '书面沟通可部分替代非正式交流，但不能替代偶然的信息碰撞',
    '一刀切政策不成立，需按岗位可远程性分层',
  ],
  disputes: [
    '分层是否必须先测量，还是可以凭经验先分',
    '早期公司的线下默契能否在规模化后被流程替代',
  ],
  basedOnSeq: 10,
}

const snapshot01: Consensus[] = [
  { version: 1, points: ['远程对弱连接的损伤有实证支持'], disputes: ['书面沟通能否替代非正式交流', '是否应按岗位分层'], basedOnSeq: 4 },
  { version: 2, points: ['远程对弱连接的损伤有实证支持', '书面沟通可部分替代，但留存不等于流通'], disputes: ['分层是否必须先测量', '早期经验是否具有普适性'], basedOnSeq: 7 },
  consensus01,
]

const panel03: Panelist[] = [
  { id: 'p_03_0', role: 'host', seat: 0, name: '苏原', title: '技术播客主理人', stance: '不预设立场，负责把「失业」拆成岗位数量与岗位结构两件事', color: '#c2410c' },
  { id: 'p_03_1', role: 'expert', seat: 1, name: '郑宇', title: '十年后端架构师', stance: '会，初级岗位的价值正被压缩', color: '#1d4ed8' },
  { id: 'p_03_2', role: 'expert', seat: 2, name: '白露', title: '计算机教育研究者', stance: '不会，AI 只是把门槛从写语法挪到做判断', color: '#7e22ce' },
  { id: 'p_03_3', role: 'expert', seat: 3, name: '黄凯', title: '创业公司技术合伙人', stance: '招聘结构会变，但总需求不减', color: '#15803d' },
  { id: 'p_03_4', role: 'expert', seat: 4, name: '顾言', title: '职业培训讲师', stance: '失业的不是初级，是不肯改工作方式的人', color: '#b45309' },
]

const panel04: Panelist[] = [
  { id: 'p_04_0', role: 'host', seat: 0, name: '沈禾', title: '劳动政策研究者', stance: '不预设立场，负责把「四天」和「少干一天」区分开', color: '#c2410c' },
  { id: 'p_04_1', role: 'expert', seat: 1, name: '罗嘉', title: '制造业人力资源总监', stance: '产线不可能四天，产能是物理约束不是管理问题', color: '#b45309' },
  { id: 'p_04_2', role: 'expert', seat: 2, name: '吴笛', title: '咨询公司合伙人', stance: '知识型岗位已经证明可行，问题在敢不敢改考核', color: '#1d4ed8' },
  { id: 'p_04_3', role: 'expert', seat: 3, name: '秦川', title: '劳动经济学讲师', stance: '关键不在天数，在可支配注意力有没有被打断', color: '#7e22ce' },
]

const panel05: Panelist[] = [
  { id: 'p_05_0', role: 'host', seat: 0, name: '方拓', title: '产品社区发起人', stance: '不预设立场，负责区分「技术背景」和「技术判断力」', color: '#c2410c' },
  { id: 'p_05_1', role: 'expert', seat: 1, name: '蒋楠', title: '大厂 P8 产品经理', stance: '需要，否则排期就是许愿', color: '#1d4ed8' },
  { id: 'p_05_2', role: 'expert', seat: 2, name: '韩束', title: '连续创业者', stance: '不需要，技术背景容易让人替研发做决定', color: '#15803d' },
  { id: 'p_05_3', role: 'expert', seat: 3, name: '段然', title: '技术转产品', stance: '需要的不是背景，是能和研发说同一种话', color: '#7e22ce' },
]

export const mockList: DiscussionSummary[] = [
  { id: 'd_01', topic: '远程办公是否应该成为公司默认工作方式', status: 'FINISHED', endedReason: 'converged', turnCount: 11, expertCount: 4, utteranceCount: 11, consensusVersion: 3, createdAt: '11-17 09:12' },
  { id: 'd_02', topic: '开源作者该不该为下游的生产事故负责', status: 'FINISHED', endedReason: 'converged', turnCount: 10, expertCount: 4, utteranceCount: 10, consensusVersion: 2, createdAt: '11-18 14:02' },
  { id: 'd_03', topic: 'AI 编程助手会让初级工程师失业吗', status: 'AWAITING_CONFIRM', endedReason: null, turnCount: 0, expertCount: 4, utteranceCount: 0, consensusVersion: 0, createdAt: '11-19 10:20' },
  { id: 'd_04', topic: '公司该不该推行四天工作制', status: 'AWAITING_CONFIRM', endedReason: null, turnCount: 0, expertCount: 3, utteranceCount: 0, consensusVersion: 0, createdAt: '11-19 15:45' },
  { id: 'd_05', topic: '产品经理需不需要技术背景', status: 'AWAITING_CONFIRM', endedReason: null, turnCount: 0, expertCount: 3, utteranceCount: 0, consensusVersion: 0, createdAt: '11-19 18:30' },
]

export const mockDetails: Record<string, DiscussionDetail> = {
  d_01: {
    ...mockList[0]!,
    panel: panel01,
    transcript: transcript01,
    consensus: consensus01,
    finalSummary:
      '这场讨论没有分出胜负，但划清了三条边界：远程对弱连接的损伤有追踪研究支持；书面沟通确实部分替代了非正式交流，但留存不等于流通；一刀切政策在岗位可远程性差异下不成立。真正未解的是阶段差异——同一家公司在二十人和两百人时的前后对照数据，今天没有人拿得出来。建议下一步先做岗位可远程性测量，再谈政策。',
    maxTurns: 24,
  },
  d_03: {
    ...mockList[2]!,
    panel: panel03,
    transcript: [],
    consensus: { version: 0, points: [], disputes: [], basedOnSeq: 0 },
    finalSummary: null,
    maxTurns: 24,
  },
  d_04: {
    ...mockList[3]!,
    panel: panel04,
    transcript: [],
    consensus: { version: 0, points: [], disputes: [], basedOnSeq: 0 },
    finalSummary: null,
    maxTurns: 24,
  },
  d_05: {
    ...mockList[4]!,
    panel: panel05,
    transcript: [],
    consensus: { version: 0, points: [], disputes: [], basedOnSeq: 0 },
    finalSummary: null,
    maxTurns: 24,
  },
}

/** 候选池：用户新发起一场时，从池里按人数取前 N 位专家（设计期占位，接后端后由模型生成）。 */
const CANDIDATE_POOL: Panelist[] = [
  { id: 'x1', role: 'expert', seat: 1, name: '方正', title: '行业研究员', stance: '先看数据再下判断，多数争论是口径不同', color: '#1d4ed8' },
  { id: 'x2', role: 'expert', seat: 2, name: '柳青', title: '一线业务负责人', stance: '落地能力才是瓶颈，方案再漂亮也要有人执行', color: '#15803d' },
  { id: 'x3', role: 'expert', seat: 3, name: '费宁', title: '独立顾问', stance: '这类问题没有标准答案，只有代价不同的选择', color: '#7e22ce' },
  { id: 'x4', role: 'expert', seat: 4, name: '尚可', title: '创业者', stance: '别谈理想状态，先看现金流撑不撑得住', color: '#b45309' },
  { id: 'x5', role: 'expert', seat: 5, name: '邵川', title: '政策研究者', stance: '个体选择和制度约束是两件事，不能混谈', color: '#be123c' },
  { id: 'x6', role: 'expert', seat: 6, name: '姜予', title: '心理学研究者', stance: '人不是理性决策者，动机比逻辑更能解释行为', color: '#0f766e' },
]

export function buildMockPanel(id: string, expertCount: number): Panelist[] {
  const host: Panelist = {
    id: `${id}_0`,
    role: 'host',
    seat: 0,
    name: '陈立',
    title: '圆桌主持 · 商业评论人',
    stance: '不预设立场，负责串场、追问与收尾',
    color: '#c2410c',
  }
  const experts = CANDIDATE_POOL.slice(0, expertCount).map((p, i) => ({
    ...p,
    id: `${id}_${i + 1}`,
    seat: i + 1,
  }))
  return [host, ...experts]
}

/**
 * 把一份已完成讨论投影成事件流 —— 与 scripts/init_db.py 的 project_events() 同规则。
 *
 * DDD 阶段用它验证"一条渲染管线"能不能跑通：回放走这个函数，
 * 后面接真 SSE 时，收到的仍是同样结构的 StudioEvent，前端无需分叉。
 */
export function projectEvents(detail: DiscussionDetail): StudioEvent[] {
  const events: StudioEvent[] = []
  let seq = 0
  let phase: string | null = null
  const snaps = Object.fromEntries(snapshot01.map((s) => [s.basedOnSeq, s]))

  for (const u of detail.transcript) {
    if (u.phase !== phase) {
      events.push({ seq: seq++, type: 'phase', phase: u.phase })
      phase = u.phase
    }
    events.push({ seq: seq++, type: 'panelist_state', panelistId: u.panelistId, state: '准备发言' })
    events.push({ seq: seq++, type: 'utterance', utterance: u })
    events.push({ seq: seq++, type: 'panelist_state', panelistId: u.panelistId, state: '待机' })

    const snap = snaps[u.seq]
    if (snap) events.push({ seq: seq++, type: 'consensus', consensus: snap })
  }

  events.push({
    seq: seq++,
    type: 'ended',
    reason: detail.endedReason ?? 'max_turns',
    turns: detail.turnCount,
  })
  return events
}
