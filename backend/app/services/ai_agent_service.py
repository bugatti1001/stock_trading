"""
AI Agent Service
核心 AI 投资讨论助手服务
支持上下文构建、多轮对话、流式输出
"""
import re
import json
import logging
from typing import Any, Iterator, List, Dict, Optional, Union

from app.config.database import db_session
from app.config.settings import AI_MAX_TOKENS, AI_TRADE_MAX_TOKENS
from app.models.stock import Stock
from app.models.conversation import Conversation, Message, ContextMode
from app.models.user_principle import UserPrinciple
from app.services.stock_analysis_service import build_stocks_summary
from app.utils.ai_helpers import build_principles_summary, parse_ai_json_response

logger = logging.getLogger(__name__)

SYSTEM_PROMPT_TEMPLATE = """你是一名专注于基本面分析的投资助手，帮助用户分析股票、建立选股框架、监督投资纪律。

【股票池数据（含持仓与关注）】
{stocks_summary}

【股票时效新闻分析】
{news_analysis_summary}
{valuation_block}
{principles_block}
规则：
- 以基本面数据为核心依据，避免纯技术分析预测
- 对情绪化决策保持中立立场并指出风险
- 如果数据不足或不确定，明确说明而不是猜测
- 用中文回复，专业但易懂
- 引用数据时请注明是来自数据库还是推断
- 如果有近期新闻分析数据，请在回答中考虑其影响，但不要让新闻情绪主导判断
- 当用户问到股票估值时，请引用内在价值和安全边际（MoS）数据进行分析
- 直接针对用户最新的问题回答，不要重述之前对话中已经说过的内容，可以一句话总结已经说过的内容
"""


def _parse_mentioned_symbols(text: str) -> List[str]:
    """解析消息中 @SYMBOL 格式的股票代码"""
    return re.findall(r'@([A-Z]{1,5})', text.upper())


def _build_system_prompt(user_message: str, context_mode: str,
                         stock_id: Optional[int] = None,
                         include_principles: bool = False) -> str:
    """根据上下文模式构建 system prompt"""
    mentioned: List[str] = _parse_mentioned_symbols(user_message)

    symbols: Optional[List[str]]
    if context_mode == 'stock' and stock_id:
        stock: Optional[Stock] = db_session.query(Stock).get(stock_id)
        symbols = [stock.symbol] if stock else None
    elif mentioned:
        symbols = mentioned
    else:
        symbols = None  # 全局：所有股票

    stocks_summary: str = build_stocks_summary(symbols)

    from app.services.news_analysis_service import build_news_analysis_summary
    news_analysis_summary: str = build_news_analysis_summary()

    # 根据对话设置决定是否注入用户投资原则
    principles_block: str = ''
    if include_principles:
        principles_text: str = build_principles_summary()
        principles_block = f"""
【用户个人投资原则】
{principles_text}

注意：以上是用户声明的个人投资原则，请在分析和建议中参考这些原则，对违反原则的操作给出提醒。
"""

    # 估值数据
    try:
        from app.services.valuation_service import build_valuation_summary_all
        valuation_text: str = build_valuation_summary_all()
        valuation_block: str = f"""
【内在价值估值】
{valuation_text}
"""
    except Exception:
        valuation_block = ''

    return SYSTEM_PROMPT_TEMPLATE.format(
        stocks_summary=stocks_summary,
        news_analysis_summary=news_analysis_summary,
        valuation_block=valuation_block,
        principles_block=principles_block,
    )


def _get_conversation_history(conversation_id: int, limit: int = 20) -> List[Dict[str, str]]:
    """获取对话历史，转换为 Anthropic messages 格式"""
    conv: Optional[Conversation] = db_session.query(Conversation).get(conversation_id)
    if not conv:
        return []

    messages: List[Message] = (db_session.query(Message)
                .filter_by(conversation_id=conversation_id)
                .order_by(Message.created_at.desc())
                .limit(limit)
                .all())
    messages.reverse()

    return [{'role': m.role, 'content': m.content} for m in messages]


def chat_stream(conversation_id: int, user_message: str) -> Iterator[str]:
    """
    流式对话，生成器返回 SSE 格式的文本块
    每次 yield 一个 data: ... 行
    """
    try:
        from app.services.ai_client import stream_message

        conv: Optional[Conversation] = db_session.query(Conversation).get(conversation_id)
        if not conv:
            yield "data: [ERROR] 对话不存在\n\n"
            return

        # 先取历史消息（保存用户消息之前），确保新消息不在其中
        history: List[Dict[str, str]] = _get_conversation_history(conversation_id, limit=18)  # 最近18条历史
        # 手动追加当前用户消息，保证它在 messages 列表末尾
        history.append({'role': 'user', 'content': user_message})

        # 保存用户消息到数据库
        user_msg: Message = Message(
            conversation_id=conversation_id,
            role='user',
            content=user_message,
        )
        db_session.add(user_msg)
        db_session.commit()

        # 构建上下文
        system_prompt: str = _build_system_prompt(
            user_message,
            conv.context_mode.value if conv.context_mode else 'global',
            conv.stock_id,
            include_principles=bool(conv.include_principles),
        )

        # 流式调用 AI（自动选择 Claude、OpenAI 或 MiniMax）
        full_response: List[str] = []
        for text in stream_message(
            system=system_prompt,
            messages=history,
            max_tokens=AI_MAX_TOKENS,
        ):
            full_response.append(text)
            # SSE 格式：每个文本块作为一个事件
            escaped: str = text.replace('\n', '\\n')
            yield f"data: {escaped}\n\n"

        # 保存 AI 回复
        assistant_content: str = ''.join(full_response)
        assistant_msg: Message = Message(
            conversation_id=conversation_id,
            role='assistant',
            content=assistant_content,
        )
        db_session.add(assistant_msg)

        # 更新对话标题（如果还是默认标题，取用户消息前20字）
        if conv.title == '新对话' and user_message:
            conv.title = user_message[:30].strip()

        db_session.commit()

        yield "data: [DONE]\n\n"

    except Exception as e:
        logger.error(f"chat_stream 错误: {e}")
        yield f"data: [ERROR] {str(e)}\n\n"


def create_conversation(title: str = '新对话', context_mode: str = 'global',
                        stock_id: Optional[int] = None,
                        include_principles: bool = False) -> Conversation:
    """新建对话"""
    mode: ContextMode = ContextMode.STOCK if context_mode == 'stock' else ContextMode.GLOBAL
    conv: Conversation = Conversation(
        title=title, context_mode=mode, stock_id=stock_id,
        include_principles=include_principles,
    )
    db_session.add(conv)
    db_session.commit()
    return conv


def get_conversations(limit: int = 50) -> List[Conversation]:
    """获取对话列表，最新优先（排除 AI 交易讨论对话）"""
    return (db_session.query(Conversation)
            .filter(Conversation.context_mode != ContextMode.AI_TRADE)
            .order_by(Conversation.updated_at.desc())
            .limit(limit)
            .all())


def rename_conversation(conversation_id: int, new_title: str) -> Optional[Conversation]:
    """重命名对话"""
    conv: Optional[Conversation] = db_session.query(Conversation).get(conversation_id)
    if not conv:
        return None
    conv.title = new_title.strip()[:200]
    db_session.commit()
    return conv


def get_messages(conversation_id: int) -> List[Message]:
    """获取对话的所有消息"""
    conv: Optional[Conversation] = db_session.query(Conversation).get(conversation_id)
    if not conv:
        return []
    return list(conv.messages)


def delete_conversation(conversation_id: int) -> bool:
    """删除对话（级联删除消息）"""
    conv: Optional[Conversation] = db_session.query(Conversation).get(conversation_id)
    if not conv:
        return False
    db_session.delete(conv)
    db_session.commit()
    return True


# ============================================================
# 交易监督
# ============================================================

def analyze_trade(trade_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    分析一笔交易是否违反投资准则
    trade_data: {
        symbol, action(buy/sell), price, quantity, trade_date,
        reason_text(用户自述理由),
        stock_data(实时数据快照), financial_data(最新财务数据)
    }
    返回: { violations, risk_score, analysis, suggestions }

    输入数据与 /agent AI讨论、/dashboard AI选股建议 完全一致：
    - 全量股票池数据（build_stocks_summary）
    - 全量投资原则（build_principles_summary）
    - 全量新闻分析（build_news_analysis_summary）
    """
    action_cn: str = '买入' if trade_data.get('action') == 'buy' else '卖出'
    symbol: str = trade_data.get('symbol', '')
    price: Union[int, float] = trade_data.get('price', 0)
    quantity: Union[int, float] = trade_data.get('quantity', 0)
    reason: str = trade_data.get('reason_text', '（用户未提供理由）')
    trade_date: str = trade_data.get('trade_date', '')

    # 与 /agent、/dashboard 完全一致的数据输入
    stocks_summary: str = build_stocks_summary()
    principles_text: str = build_principles_summary()

    from app.services.news_analysis_service import build_news_analysis_summary
    news_summary: str = build_news_analysis_summary()

    # 估值数据
    try:
        from app.services.valuation_service import build_valuation_summary_all
        valuation_summary: str = build_valuation_summary_all()
    except Exception:
        valuation_summary = '（估值数据暂不可用）'

    prompt: str = f"""你是一名严格遵守投资纪律的价值投资基金经理。请客观评估以下交易操作是否是一个好的投资决策。

【核心投资原则 — 评估标准】
{principles_text}

【本次交易】
操作: {action_cn} {symbol}
价格: ${price}  数量: {quantity}股  日期: {trade_date}
用户理由: "{reason}"

【持仓股票池完整数据】
{stocks_summary}

【近期新闻分析】
{news_summary}

【内在价值估值】
{valuation_summary}

请站在基金经理的角度，客观评估这笔交易：
1. 这笔交易是否符合投资原则（逐条对照，符合的也要说明）
2. 基本面数据和内在价值估值是否支持此操作（内在价值、安全边际MoS、盈利、财务健康、护城河）
3. 近期新闻是否支持此操作的时机
4. 用户理由是否合理，是否存在情绪化因素
5. 综合判断：如果你是基金经理，你会做同样的操作吗？

评分标准：
- risk_score 0-30：优秀的交易决策，完全符合原则且时机合理
- risk_score 30-50：可以接受的交易，基本符合原则
- risk_score 50-70：有一定风险，部分不符合原则或时机欠佳
- risk_score 70-100：高风险交易，明显违反原则或基本面不支持

只返回 JSON，格式如下：
{{
  "violations": ["违规条目1", "违规条目2"],  // 空数组表示无违规
  "risk_score": 45,  // 0-100，越高越危险
  "analysis": "2-3句话的综合分析",
  "suggestions": "1-2句改进建议"
}}"""

    try:
        from app.services.ai_client import create_message
        raw: str = create_message(
            messages=[{'role': 'user', 'content': prompt}],
            max_tokens=2048,
        )
        logger.info(f"[analyze_trade] {symbol} AI返回 {len(raw)} 字符")
        result = parse_ai_json_response(raw)
        if not result or not isinstance(result, dict):
            logger.error(f"[analyze_trade] {symbol} JSON解析失败, 原始: {raw[:300]}")
            return {'error': f'AI 返回格式错误', 'raw': raw[:200]}
        # 确保必要字段存在
        if 'risk_score' not in result and 'analysis' not in result:
            logger.error(f"[analyze_trade] {symbol} 返回缺少必要字段: {result}")
            return {'error': 'AI 返回缺少必要字段', 'raw': str(result)[:200]}
        return result
    except Exception as e:
        logger.error(f"analyze_trade 失败: {e}", exc_info=True)
        return {'error': str(e)}


# ============================================================
# 第四期：用户投资原则
# ============================================================

def extract_principles(conversation_id: int) -> Dict[str, Any]:
    """
    从指定对话历史中提炼用户个人投资原则
    返回: { principles: [{ title, content, category }], error? }
    """
    conv: Optional[Conversation] = db_session.query(Conversation).get(conversation_id)
    if not conv:
        return {'error': '对话不存在'}

    # 只取 AI 助手最后一条回复
    last_assistant_msg: Optional[Message] = (db_session.query(Message)
                          .filter_by(conversation_id=conversation_id, role='assistant')
                          .order_by(Message.created_at.desc())
                          .first())

    if not last_assistant_msg:
        return {'error': '当前对话还没有 AI 回复，请先开始对话'}

    prompt: str = f"""以下是投资 AI 助手的一段分析内容：

{last_assistant_msg.content}

请从这段内容中，提炼出可作为个人投资原则的要点。

要求：
- 每条原则要具体可操作（不是泛泛而谈，如"价值投资"太宽泛）
- 用第一人称描述（"我不会..."、"我偏好..."、"我要求..."）
- category 只能填以下之一：risk（风险管理）/ valuation（估值）/ selection（选股）/ behavior（投资行为）
- 如果内容中没有可提炼的投资原则，返回空数组 []
- 不要重复相似的原则，合并同类项

只返回 JSON 数组，格式：
[{{"title": "原则标题（10字以内）", "content": "具体描述（50字以内）", "category": "behavior"}}]"""

    try:
        from app.services.ai_client import create_message
        raw: str = create_message(
            messages=[{'role': 'user', 'content': prompt}],
            max_tokens=AI_TRADE_MAX_TOKENS,
        )
        principles = parse_ai_json_response(raw)
        if not isinstance(principles, list):
            return {'error': 'AI 返回格式不是数组'}

        return {'principles': principles, 'source_conv_id': conversation_id}
    except json.JSONDecodeError as e:
        logger.error(f"extract_principles JSON解析失败: {e}")
        return {'error': f'AI 返回格式错误: {e}'}
    except Exception as e:
        logger.error(f"extract_principles 失败: {e}", exc_info=True)
        return {'error': str(e)}


# ============================================================
# AI 交易讨论
# ============================================================

AI_TRADE_DISCUSSION_PROMPT = """你是一名专业基金经理，管理一个模拟投资组合。用户正在询问你之前做出的一笔 AI 模拟交易的决策原因。

【本次讨论的交易】
股票: {symbol}
操作: {action_cn}
数量: {shares} 股
价格: ${price}
金额: ${amount}
交易日期: {trade_date}
当时的决策原因: {reason}

【AI 模拟账户信息】
总资金: ${total_capital}
AI持仓总价值: ${ai_portfolio_value}
AI可用现金: ${ai_available_cash}

【股票池评分及AI当前持仓】
{stocks_text}

【用户投资原则】
{principles}

【近期新闻分析】
{news}

【内在价值估值】
{valuation_summary}

规则：
- 解释你做出这笔交易的具体逻辑和考量依据
- 引用内在价值和安全边际（MoS）数据来支撑你的买入/卖出判断
- 如果用户提出质疑，坦诚分析可能的不足
- 结合当前数据和交易时的数据变化，评估这笔交易目前的表现
- 用中文回复，专业但易懂
- 直接针对用户最新的问题回答，不要重述已说过的内容"""


def _build_ai_trade_system_prompt(trade) -> str:
    """构建 AI 交易讨论的 system prompt，复用 generate_ai_trades 的数据上下文"""
    from app.services.stock_scorer import score_all_stocks, compute_ai_holdings
    from app.services.news_analysis_service import build_news_analysis_summary
    from app.models.user_setting import UserSetting

    action_cn = '买入' if trade.action == 'buy' else '卖出'

    # 总资金
    try:
        row = db_session.query(UserSetting).filter_by(key='total_capital').first()
        total_capital = float(row.value) if row else 0
    except Exception:
        total_capital = 0

    # AI 持仓
    ai_holdings = compute_ai_holdings()

    # 评分
    try:
        scored_stocks = score_all_stocks()
    except Exception:
        scored_stocks = []

    # 价格映射
    price_map = {}
    missing_price_symbols = []
    for s in scored_stocks:
        h = s.get('holding')
        if h and h.get('current_price'):
            price_map[s['symbol']] = h['current_price']
        else:
            missing_price_symbols.append(s['symbol'])
    if missing_price_symbols:
        try:
            for stock_obj in db_session.query(Stock).filter(
                Stock.symbol.in_(missing_price_symbols)
            ).all():
                if stock_obj.current_price:
                    price_map[stock_obj.symbol] = stock_obj.current_price
        except Exception:
            pass

    ai_portfolio_value = sum(
        ai_holdings.get(sym, {}).get('shares', 0) * price_map.get(sym, 0)
        for sym in ai_holdings
    )
    ai_available_cash = max(0, total_capital - ai_portfolio_value)

    # 估值数据
    try:
        from app.services.valuation_service import valuate_all_stocks, build_valuation_summary_all
        val_map = {v['symbol']: v for v in valuate_all_stocks()}
        valuation_summary = build_valuation_summary_all()
    except Exception as e:
        logger.warning(f"_build_ai_trade_system_prompt 估值计算失败: {e}")
        val_map = {}
        valuation_summary = '（估值数据暂不可用）'

    # 股票行
    def _stock_line(s):
        price = price_map.get(s['symbol'], 0)
        # 估值信息
        val = val_map.get(s['symbol'])
        if val:
            iv = val.get('composite', {}).get('intrinsic_value')
            mos = val.get('margin_of_safety', {})
            mos_pct = mos.get('pct')
            mos_label = mos.get('signal_label', '')
            if iv and mos_pct is not None:
                val_info = f", 内在价值${iv:.0f}(MoS{mos_pct:+.0f}%{mos_label})"
            else:
                val_info = ", 内在价值:N/A"
        else:
            val_info = ", 内在价值:N/A"
        # AI持仓信息
        ai_h = ai_holdings.get(s['symbol'], {})
        ai_shares = ai_h.get('shares', 0)
        ai_cost = ai_h.get('avg_cost', 0)
        ai_pnl = ''
        if ai_shares > 0 and ai_cost > 0 and price > 0:
            pnl_pct = (price - ai_cost) / ai_cost * 100
            ai_pnl = f", AI持仓{ai_shares}股(成本${ai_cost:.2f}, 盈亏{pnl_pct:+.1f}%)"
        elif ai_shares > 0:
            ai_pnl = f", AI持仓{ai_shares}股"
        return f"{s['symbol']}({s.get('stock_name', '')}): 评分{s['total_score']}, 现价${price}{val_info}{ai_pnl}"

    stocks_text = '\n'.join(_stock_line(s) for s in scored_stocks) if scored_stocks else '（暂无评分数据）'
    principles = build_principles_summary()
    news = build_news_analysis_summary()

    return AI_TRADE_DISCUSSION_PROMPT.format(
        symbol=trade.symbol,
        action_cn=action_cn,
        shares=trade.shares,
        price=trade.price,
        amount=round(trade.shares * trade.price, 2),
        trade_date=trade.trade_date.isoformat() if trade.trade_date else '未知',
        reason=trade.reason or '（未记录原因）',
        total_capital=f'{total_capital:,.0f}',
        ai_portfolio_value=f'{ai_portfolio_value:,.0f}',
        ai_available_cash=f'{ai_available_cash:,.0f}',
        stocks_text=stocks_text,
        principles=principles,
        news=news,
        valuation_summary=valuation_summary,
    )


def ai_trade_chat_stream(trade_id: int, user_message: str) -> Iterator[str]:
    """AI 交易讨论流式对话，自动创建/复用与该交易绑定的对话"""
    try:
        from app.services.ai_client import stream_message
        from app.models.ai_trade_record import AiTradeRecord

        trade = db_session.query(AiTradeRecord).get(trade_id)
        if not trade:
            yield "data: [ERROR] 交易记录不存在\n\n"
            return

        # 查找或创建对话
        conv = db_session.query(Conversation).filter_by(ai_trade_id=trade_id).first()
        if not conv:
            action_cn = '买入' if trade.action == 'buy' else '卖出'
            conv = Conversation(
                title=f'{trade.symbol} {action_cn} 讨论',
                context_mode=ContextMode.AI_TRADE,
                ai_trade_id=trade_id,
                include_principles=True,
            )
            db_session.add(conv)
            db_session.commit()

        # 获取历史
        history = _get_conversation_history(conv.id, limit=18)
        history.append({'role': 'user', 'content': user_message})

        # 保存用户消息
        user_msg = Message(conversation_id=conv.id, role='user', content=user_message)
        db_session.add(user_msg)
        db_session.commit()

        # 构建 system prompt
        system_prompt = _build_ai_trade_system_prompt(trade)

        # 流式调用 AI
        full_response = []
        for text in stream_message(system=system_prompt, messages=history, max_tokens=AI_MAX_TOKENS):
            full_response.append(text)
            escaped = text.replace('\n', '\\n')
            yield f"data: {escaped}\n\n"

        # 保存 AI 回复
        assistant_content = ''.join(full_response)
        assistant_msg = Message(conversation_id=conv.id, role='assistant', content=assistant_content)
        db_session.add(assistant_msg)
        db_session.commit()

        yield "data: [DONE]\n\n"

    except Exception as e:
        logger.error(f"ai_trade_chat_stream 错误: {e}")
        yield f"data: [ERROR] {str(e)}\n\n"


def generate_dashboard_insight() -> str:
    """
    生成 Dashboard AI 选股建议。
    直接从 AI 今日交易记录生成，与 AI 交易共用同一套决策结果，不再独立调 AI。
    如果今天还没有 AI 交易记录，提示用户先执行 AI 交易。
    """
    from datetime import date as date_type
    from app.models.ai_trade_record import AiTradeRecord

    today = date_type.today()
    today_records = db_session.query(AiTradeRecord).filter(
        AiTradeRecord.trade_date == today,
        ~AiTradeRecord.reason.like('%初始化%'),
        ~AiTradeRecord.reason.like('%重置%'),
    ).all()

    if not today_records:
        return '📭 今日尚未执行 AI 模拟交易。请先点击「AI 交易」生成今日交易决策，选股建议将基于交易结果生成。'

    # 按买入/卖出分组
    buys = [r for r in today_records if r.action == 'buy']
    sells = [r for r in today_records if r.action == 'sell']

    lines = []
    if buys:
        lines.append('### 🟢 AI 建议买入/加仓\n')
        for r in buys:
            amount = round(r.shares * r.price, 2)
            lines.append(f'- **{r.symbol}** — {r.shares}股 @${r.price:.2f}（${amount:,.0f}）')
            if r.reason:
                lines.append(f'  > {r.reason}')
            lines.append('')

    if sells:
        lines.append('### 🔴 AI 建议卖出/减仓\n')
        for r in sells:
            amount = round(r.shares * r.price, 2)
            lines.append(f'- **{r.symbol}** — {r.shares}股 @${r.price:.2f}（${amount:,.0f}）')
            if r.reason:
                lines.append(f'  > {r.reason}')
            lines.append('')

    holds = [r for r in today_records if r.action == 'hold']
    if holds:
        hold_symbols = ', '.join(r.symbol for r in holds)
        lines.append(f'### ⚪ 维持持仓不变：{hold_symbols}\n')

    lines.append(f'---\n*基于 {today.strftime("%Y-%m-%d")} AI 模拟交易决策，共 {len(buys)} 买 / {len(sells)} 卖*')

    return '\n'.join(lines)
