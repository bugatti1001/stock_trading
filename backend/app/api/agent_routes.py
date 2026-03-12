"""
AI Agent API Routes
提供 AI 投资讨论的 REST + SSE 端点
"""
import logging
from flask import Blueprint, request, Response, stream_with_context

from app.services import ai_agent_service
from app.utils.response import success_response, error_response

logger = logging.getLogger(__name__)

bp = Blueprint('agent', __name__)


@bp.route('/api/agent/conversations', methods=['GET'])
def list_conversations():
    """列出历史对话"""
    try:
        convs = ai_agent_service.get_conversations()
        return success_response(conversations=[c.to_dict() for c in convs])
    except Exception as e:
        logger.error(f"list_conversations 错误: {e}")
        return error_response(str(e), 500)


@bp.route('/api/agent/conversations', methods=['POST'])
def create_conversation():
    """新建对话"""
    try:
        data = request.get_json() or {}
        title = data.get('title', '新对话')
        context_mode = data.get('context_mode', 'global')
        stock_id = data.get('stock_id')
        include_principles = bool(data.get('include_principles', False))

        conv = ai_agent_service.create_conversation(
            title, context_mode, stock_id,
            include_principles=include_principles,
        )
        return success_response(conversation=conv.to_dict(), status_code=201)
    except Exception as e:
        logger.error(f"create_conversation 错误: {e}")
        return error_response(str(e), 500)


@bp.route('/api/agent/conversations/<int:conv_id>', methods=['PATCH'])
def rename_conversation(conv_id: int):
    """重命名对话"""
    try:
        data = request.get_json() or {}
        new_title = data.get('title', '').strip()
        if not new_title:
            return error_response('标题不能为空', 400)

        conv = ai_agent_service.rename_conversation(conv_id, new_title)
        if not conv:
            return error_response('对话不存在', 404)
        return success_response(conversation=conv.to_dict())
    except Exception as e:
        logger.error(f"rename_conversation 错误: {e}")
        return error_response(str(e), 500)


@bp.route('/api/agent/conversations/<int:conv_id>', methods=['DELETE'])
def delete_conversation(conv_id: int):
    """删除对话"""
    try:
        ok = ai_agent_service.delete_conversation(conv_id)
        if not ok:
            return error_response('对话不存在', 404)
        return success_response()
    except Exception as e:
        logger.error(f"delete_conversation 错误: {e}")
        return error_response(str(e), 500)


@bp.route('/api/agent/conversations/<int:conv_id>/messages', methods=['GET'])
def get_messages(conv_id: int):
    """获取对话消息列表"""
    try:
        msgs = ai_agent_service.get_messages(conv_id)
        return success_response(messages=[m.to_dict() for m in msgs])
    except Exception as e:
        logger.error(f"get_messages 错误: {e}")
        return error_response(str(e), 500)


@bp.route('/api/agent/conversations/<int:conv_id>/chat', methods=['POST'])
def chat(conv_id: int):
    """
    流式聊天端点（SSE）
    请求体: { "message": "用户消息" }
    响应: text/event-stream
    """
    data = request.get_json()
    if not data or not data.get('message'):
        return error_response('缺少 message 字段', 400)

    user_message = data['message'].strip()
    if not user_message:
        return error_response('消息不能为空', 400)

    def generate():
        yield from ai_agent_service.chat_stream(conv_id, user_message)

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive',
        }
    )


@bp.route('/api/agent/conversations/<int:conv_id>/extract_principles', methods=['POST'])
def extract_principles(conv_id: int):
    """从指定对话中提炼用户投资原则"""
    try:
        result = ai_agent_service.extract_principles(conv_id)
        if 'error' in result:
            return error_response(result['error'], 500)
        return success_response(
            principles=result['principles'],
            source_conv_id=result.get('source_conv_id')
        )
    except Exception as e:
        logger.error(f"extract_principles 错误: {e}")
        return error_response(str(e), 500)


@bp.route('/api/agent/dashboard_insight', methods=['GET'])
def dashboard_insight():
    """生成 Dashboard AI 选股建议（非流式）"""
    try:
        insight = ai_agent_service.generate_dashboard_insight()
        return success_response(insight=insight)
    except Exception as e:
        logger.error(f"dashboard_insight 错误: {e}")
        return error_response(str(e), 500)


# ============================================================
# 每日评分推荐
# ============================================================

@bp.route('/api/agent/daily_scores', methods=['GET'])
def daily_scores():
    """获取股票池评分推荐列表"""
    try:
        from app.services.stock_scorer import score_all_stocks
        scores = score_all_stocks()
        return success_response(scores=scores)
    except Exception as e:
        logger.error(f"daily_scores 错误: {e}")
        return error_response(str(e), 500)


@bp.route('/api/agent/daily_scores/ai_reasoning', methods=['POST'])
def daily_scores_ai_reasoning():
    """基于评分结果生成 AI 补充分析"""
    try:
        from app.services.stock_scorer import score_all_stocks, generate_ai_recommendations
        scores = score_all_stocks()
        reasoning = generate_ai_recommendations(scores)
        return success_response(reasoning=reasoning)
    except Exception as e:
        logger.error(f"daily_scores_ai_reasoning 错误: {e}")
        return error_response(str(e), 500)


@bp.route('/api/agent/scorer_weights', methods=['GET'])
def get_scorer_weights():
    """获取当前评分权重"""
    try:
        from app.services.stock_scorer import get_user_weights, DIMENSION_LABELS
        weights = get_user_weights()
        return success_response(
            weights=weights,
            labels=DIMENSION_LABELS,
        )
    except Exception as e:
        logger.error(f"get_scorer_weights 错误: {e}")
        return error_response(str(e), 500)


@bp.route('/api/agent/scorer_weights', methods=['PUT'])
def update_scorer_weights():
    """更新评分权重"""
    try:
        data = request.get_json()
        if not data or 'weights' not in data:
            return error_response('缺少 weights 字段', 400)

        from app.services.stock_scorer import save_user_weights
        saved = save_user_weights(data['weights'])
        return success_response(weights=saved)
    except Exception as e:
        logger.error(f"update_scorer_weights 错误: {e}")
        return error_response(str(e), 500)
