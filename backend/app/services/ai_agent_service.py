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
from app.config.settings import AI_MODEL, AI_MAX_TOKENS, AI_TRADE_MAX_TOKENS, get_anthropic_key
from app.models.stock import Stock
from app.models.conversation import Conversation, Message, ContextMode
from app.models.user_principle import UserPrinciple
from app.services.stock_analysis_service import build_stocks_summary
from app.utils.ai_helpers import build_principles_summary, parse_ai_json_response

logger = logging.getLogger(__name__)

SYSTEM_PROMPT_TEMPLATE = """你是一名专注于基本面分析的投资助手，帮助用户分析股票、建立选股框架、监督投资纪律。

【当前持仓股票数据】
{stocks_summary}

【股票时效新闻分析】
{news_analysis_summary}
{principles_block}
规则：
- 以基本面数据为核心依据，避免纯技术分析预测
- 对情绪化决策保持中立立场并指出风险
- 如果数据不足或不确定，明确说明而不是猜测
- 用中文回复，专业但易懂
- 引用数据时请注明是来自数据库还是推断
- 如果有近期新闻分析数据，请在回答中考虑其影响，但不要让新闻情绪主导判断
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

    return SYSTEM_PROMPT_TEMPLATE.format(
        stocks_summary=stocks_summary,
        news_analysis_summary=news_analysis_summary,
        principles_block=principles_block,
    )


def _get_conversation_history(conversation_id: int, limit: int = 20) -> List[Dict[str, str]]:
    """获取对话历史，转换为 Anthropic messages 格式"""
    conv: Optional[Conversation] = db_session.query(Conversation).get(conversation_id)
    if not conv:
        return []

    messages: List[Message] = (conv.messages
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
    api_key: str = get_anthropic_key()
    if not api_key:
        yield "data: [ERROR] 未配置 ANTHROPIC_API_KEY\n\n"
        return

    try:
        import anthropic
        client: anthropic.Anthropic = anthropic.Anthropic(api_key=api_key)

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

        # 流式调用 Claude
        full_response: List[str] = []
        with client.messages.stream(
            model=AI_MODEL,
            max_tokens=AI_MAX_TOKENS,
            system=system_prompt,
            messages=history,
        ) as stream:
            for text in stream.text_stream:
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
    """获取对话列表，最新优先"""
    return (db_session.query(Conversation)
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
    api_key: str = get_anthropic_key()
    if not api_key:
        return {'error': '未配置 ANTHROPIC_API_KEY'}

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

    prompt: str = f"""你是一名严格的投资纪律审查官。请分析以下交易操作是否符合用户的个人投资原则。

【本次交易】
操作: {action_cn} {symbol}
价格: ${price}  数量: {quantity}股  日期: {trade_date}
用户理由: "{reason}"

【持仓股票池完整数据】
{stocks_summary}

【用户个人投资原则】
{principles_text}

【近期新闻分析】
{news_summary}

请重点针对 {symbol} 这只股票，从以下角度评审：
1. 用户理由是否包含情绪化因素（追涨杀跌、恐惧贪婪）
2. 当前估值是否合理（PE/PB/PS 是否过高/过低，结合行业对比）
3. 基本面是否支持此次操作（盈利质量、财务健康、护城河、资本配置等完整维度）
4. 是否违反了用户自己声明的个人投资原则（逐条对照）
5. 近期新闻事件是否影响此次交易决策的合理性
6. 如果是买入，该股票在整个持仓组合中的定位是否合理

只返回 JSON，格式如下：
{{
  "violations": ["违规条目1", "违规条目2"],  // 空数组表示无违规
  "risk_score": 45,  // 0-100，越高越危险
  "analysis": "2-3句话的综合分析",
  "suggestions": "1-2句改进建议"
}}"""

    try:
        import anthropic
        client: anthropic.Anthropic = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model=AI_MODEL,
            max_tokens=AI_TRADE_MAX_TOKENS,
            messages=[{'role': 'user', 'content': prompt}]
        )
        raw: str = msg.content[0].text.strip()
        return parse_ai_json_response(raw)
    except json.JSONDecodeError as e:
        logger.error(f"analyze_trade JSON解析失败: {e}, 原始内容: {raw[:200] if 'raw' in locals() else 'N/A'}")
        return {'error': f'AI 返回格式错误: {e}'}
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
    api_key: str = get_anthropic_key()
    if not api_key:
        return {'error': '未配置 ANTHROPIC_API_KEY'}

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
        import anthropic
        client: anthropic.Anthropic = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model=AI_MODEL,
            max_tokens=AI_TRADE_MAX_TOKENS,
            messages=[{'role': 'user', 'content': prompt}]
        )
        raw: str = msg.content[0].text.strip()
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


def generate_dashboard_insight() -> str:
    """
    生成 Dashboard AI 选股建议
    基于：持仓股票数据 + 激活的 UserPrinciple
    返回 Markdown 格式字符串
    """
    api_key: str = get_anthropic_key()
    if not api_key:
        return '⚠️ 未配置 ANTHROPIC_API_KEY，无法生成建议。'

    stocks_summary: str = build_stocks_summary()
    principles_summary: str = build_principles_summary()

    from app.services.news_analysis_service import build_news_analysis_summary
    news_summary: str = build_news_analysis_summary()

    if stocks_summary == "（暂无持仓股票数据）":
        return '📭 股票池暂无数据，请先在「股票池」中添加股票并刷新财务数据。'

    from datetime import datetime, timezone
    today_str: str = datetime.now(timezone.utc).strftime('%Y-%m-%d (%A)')

    prompt: str = f"""你是一名基本面投资助手。请根据以下信息，给出今日（{today_str}）持仓关注建议。

【持仓股票数据】
{stocks_summary}

【用户个人投资原则】
{principles_summary}

【近期新闻分析】
{news_summary}

请：
1. 从持仓股票中，标出 2-3 只当前最值得重点关注的（结合基本面数据和近期新闻给出具体理由）
2. 标出 1-2 只需要警惕的（估值偏高、基本面走弱、负面新闻或违反用户原则的信号）
3. 一句话总结当前持仓整体质量

要求：
- 今天是 {today_str}，请结合当日新闻热点和市场环境给出有时效性的建议
- 只基于上方数据作出判断，数据不足时说明
- 用 Markdown 格式，简洁专业，不超过 300 字
- 标题用 **加粗**，用 emoji 辅助区分（✅ 关注 / ⚠️ 警惕）
- 在评判时，以用户的个人投资原则为核心标准
- 如果有近期新闻数据，在建议中体现新闻信息的影响"""

    try:
        import anthropic
        client: anthropic.Anthropic = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model=AI_MODEL,
            max_tokens=AI_TRADE_MAX_TOKENS,
            messages=[{'role': 'user', 'content': prompt}]
        )
        return msg.content[0].text.strip()
    except Exception as e:
        logger.error(f"generate_dashboard_insight 失败: {e}", exc_info=True)
        return f'⚠️ AI 建议生成失败：{str(e)}'
