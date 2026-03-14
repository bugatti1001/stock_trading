"""
用户投资原则 CRUD API
GET    /api/principles               — 列出所有原则
POST   /api/principles               — 批量创建原则（list）
GET    /api/principles/export        — 导出原则为 JSON 文件下载
POST   /api/principles/import        — 导入 JSON 文件中的原则
POST   /api/principles/deduplicate   — AI 语义去重
PUT    /api/principles/<id>          — 编辑原则
DELETE /api/principles/<id>          — 删除原则
PATCH  /api/principles/<id>/toggle   — 切换 is_active
"""
import json
import logging
from flask import Blueprint, request, Response
from sqlalchemy import desc

from app.config.database import db_session
from app.models.user_principle import UserPrinciple
from app.utils.response import success_response, error_response
from app.config.settings import VALID_CATEGORIES

logger = logging.getLogger(__name__)
bp = Blueprint('principles', __name__)


@bp.route('/api/principles', methods=['GET'])
def list_principles() -> tuple:
    try:
        principles = db_session.query(UserPrinciple).order_by(
            desc(UserPrinciple.created_at)
        ).all()
        return success_response(principles=[p.to_dict() for p in principles])
    except Exception as e:
        logger.error(f"list_principles error: {e}")
        return error_response(str(e), 500)


@bp.route('/api/principles', methods=['POST'])
def create_principles() -> tuple:
    """
    接受单条或数组：
      { "title": "...", "content": "...", "category": "...", "source_conv_id": null }
      或
      [{ ... }, { ... }]
    """
    try:
        data = request.get_json()
        if not data:
            return error_response('缺少请求体')

        items = data if isinstance(data, list) else [data]
        created = []
        for item in items:
            if not item.get('title') or not item.get('content'):
                continue

            title = item['title'].strip()
            content = item['content'].strip()

            if len(title) > 100:
                return error_response('标题不能超过100个字符')
            if len(content) > 2000:
                return error_response('内容不能超过2000个字符')

            category = item.get('category')
            if category is not None and category not in VALID_CATEGORIES:
                return error_response(f'无效的分类: {category}')

            p = UserPrinciple(
                title=title,
                content=content,
                category=category,
                is_active=item.get('is_active', True),
                source_conv_id=item.get('source_conv_id'),
            )
            db_session.add(p)
            created.append(p)

        db_session.commit()
        return success_response(
            principles=[p.to_dict() for p in created],
            status_code=201,
        )
    except Exception as e:
        db_session.rollback()
        logger.error(f"create_principles error: {e}")
        return error_response(str(e), 500)


@bp.route('/api/principles/export', methods=['GET'])
def export_principles() -> Response:
    """导出所有原则为 JSON 文件下载"""
    try:
        principles = db_session.query(UserPrinciple).order_by(UserPrinciple.created_at).all()
        data = [{
            'title': p.title,
            'content': p.content,
            'category': p.category,
            'is_active': p.is_active
        } for p in principles]
        json_str = json.dumps(data, ensure_ascii=False, indent=2)
        return Response(
            json_str,
            mimetype='application/json',
            headers={'Content-Disposition': 'attachment; filename=my_principles.json'}
        )
    except Exception as e:
        logger.error(f"export_principles error: {e}")
        return error_response(str(e), 500)


@bp.route('/api/principles/import', methods=['POST'])
def import_principles() -> tuple:
    """导入 JSON 文件中的原则"""
    try:
        file = request.files.get('file')
        if not file:
            return error_response('请选择 JSON 文件')

        try:
            raw_bytes = file.read()
            logger.info(f"[import] file.filename={file.filename}, raw_bytes length={len(raw_bytes)}")
            # Strip UTF-8 BOM if present
            if raw_bytes.startswith(b'\xef\xbb\xbf'):
                raw_bytes = raw_bytes[3:]
            file_content = raw_bytes.decode('utf-8')
            logger.info(f"[import] decoded string length={len(file_content)}, first 200 chars: {file_content[:200]}")
            data = json.loads(file_content)
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            logger.error(f"[import] parse error: {e}")
            return error_response(f'JSON 文件格式错误: {e}')

        if not isinstance(data, list):
            logger.warning(f"[import] data is not a list, type={type(data)}")
            return error_response('文件内容必须是 JSON 数组')

        logger.info(f"[import] parsed {len(data)} items from JSON array")
        created = []
        for idx, item in enumerate(data):
            if not isinstance(item, dict):
                logger.warning(f"[import] item {idx} is not a dict: {type(item)}")
                continue
            title = (item.get('title') or '').strip()
            content_text = (item.get('content') or '').strip()
            if not title or not content_text:
                logger.warning(f"[import] item {idx} skipped: title='{title[:30]}' content_empty={not content_text}")
                continue
            cat = item.get('category')
            if cat is not None and cat not in VALID_CATEGORIES:
                cat = None
            p = UserPrinciple(
                title=title[:100],
                content=content_text,
                category=cat,
                is_active=bool(item.get('is_active', True)),
            )
            db_session.add(p)
            created.append(p)

        if not created:
            logger.warning(f"[import] no valid items created from {len(data)} items")
            if len(data) == 0:
                return error_response('文件中没有有效的原则条目（JSON 数组为空）')
            return error_response(
                f'文件中没有有效的原则条目（共 {len(data)} 条，每条需包含非空 title 和 content 字段）'
            )

        db_session.commit()
        logger.info(f"[import] successfully imported {len(created)} principles")
        return success_response(
            imported=len(created),
            principles=[p.to_dict() for p in created],
            status_code=201,
        )
    except Exception as e:
        db_session.rollback()
        logger.error(f"import_principles error: {e}")
        return error_response(str(e), 500)


@bp.route('/api/principles/deduplicate', methods=['POST'])
def deduplicate_principles() -> tuple:
    """
    AI 语义去重 — 预览 / 确认 两步模式
    step 1 (无 confirm 字段): AI 分析并返回重复组预览，不做任何修改
    step 2 (confirm=true + groups): 根据用户确认的 groups 执行合并删除
    """
    try:
        data = request.get_json() or {}

        # ======== step 2: 用户确认后执行 ========
        if data.get('confirm'):
            groups = data.get('groups', [])
            if not groups:
                return success_response(removed=0, kept=0, principles=[])

            principles = db_session.query(UserPrinciple).all()
            principle_map = {p.id: p for p in principles}
            removed_count = 0

            for g in groups:
                keep_id = g.get('keep_id')
                delete_ids = g.get('delete_ids', [])
                new_title = g.get('new_title')
                new_content = g.get('new_content')

                kept = principle_map.get(keep_id)
                if not kept:
                    continue

                if new_title:
                    kept.title = new_title.strip()
                if new_content:
                    kept.content = new_content.strip()

                for did in delete_ids:
                    p_del = principle_map.get(did)
                    if p_del and p_del.id != keep_id:
                        db_session.delete(p_del)
                        removed_count += 1

            db_session.commit()
            remaining = db_session.query(UserPrinciple).order_by(desc(UserPrinciple.created_at)).all()
            return success_response(
                removed=removed_count,
                kept=len(remaining),
                principles=[p.to_dict() for p in remaining],
            )

        # ======== step 1: AI 分析，返回预览 ========
        principles = db_session.query(UserPrinciple).order_by(UserPrinciple.created_at).all()
        if len(principles) <= 1:
            return success_response(
                groups=[],
                principles=[p.to_dict() for p in principles],
            )

        principles_text = "\n".join(
            f'{p.id}. [{p.category}] 标题：{p.title} | 内容：{p.content}'
            for p in principles
        )

        prompt = f"""以下是用户的投资原则列表（每条有唯一ID）：

{principles_text}

请对这些原则做语义去重，规则：
1. 识别表达相同或高度相似投资理念的原则（即使措辞不同）
2. 将重复的一组合并为一条，使用最完整、最准确的描述
3. 合并后的原则从该组中选一个ID作为"保留ID"，其余为"删除ID"
4. 如果合并后内容需要改进，提供新的 title 和 content（否则保持原样）

只返回 JSON，格式：
{{
  "groups": [
    {{
      "keep_id": 1,
      "delete_ids": [9, 12],
      "new_title": "可选，如需改进标题",
      "new_content": "可选，如需改进内容"
    }}
  ]
}}

如果某条原则无需合并，不要出现在 groups 里。
只返回 JSON，不要任何解释文字。"""

        from app.services.ai_client import create_message
        raw = create_message(
            messages=[{'role': 'user', 'content': prompt}],
            max_tokens=2048,
        )

        from app.utils.ai_helpers import parse_ai_json_response
        result = parse_ai_json_response(raw)
        groups = result.get('groups', [])

        # 为每个 group 附上原则的 title/content 方便前端展示
        principle_map = {p.id: p for p in principles}
        for g in groups:
            kept = principle_map.get(g.get('keep_id'))
            g['keep_title'] = kept.title if kept else ''
            g['keep_content'] = kept.content if kept else ''
            g['delete_items'] = []
            for did in g.get('delete_ids', []):
                p = principle_map.get(did)
                if p:
                    g['delete_items'].append({'id': p.id, 'title': p.title, 'content': p.content})

        return success_response(
            groups=groups,
            principles=[p.to_dict() for p in principles],
        )

    except json.JSONDecodeError as e:
        db_session.rollback()
        logger.error(f"deduplicate_principles JSON解析失败: {e}")
        return error_response(f'AI 返回格式错误: {e}', 500)
    except Exception as e:
        db_session.rollback()
        logger.error(f"deduplicate_principles error: {e}", exc_info=True)
        return error_response(str(e), 500)


@bp.route('/api/principles/<int:principle_id>', methods=['PUT'])
def update_principle(principle_id: int) -> tuple:
    try:
        p = db_session.query(UserPrinciple).get(principle_id)
        if not p:
            return error_response('原则不存在', 404)

        data = request.get_json() or {}
        if 'title' in data:
            p.title = data['title'].strip()
        if 'content' in data:
            p.content = data['content'].strip()
        if 'category' in data:
            p.category = data['category']
        if 'is_active' in data:
            p.is_active = bool(data['is_active'])

        db_session.commit()
        return success_response(principle=p.to_dict())
    except Exception as e:
        db_session.rollback()
        logger.error(f"update_principle error: {e}")
        return error_response(str(e), 500)


@bp.route('/api/principles/<int:principle_id>', methods=['DELETE'])
def delete_principle(principle_id: int) -> tuple:
    try:
        p = db_session.query(UserPrinciple).get(principle_id)
        if not p:
            return error_response('原则不存在', 404)
        db_session.delete(p)
        db_session.commit()
        return success_response()
    except Exception as e:
        db_session.rollback()
        logger.error(f"delete_principle error: {e}")
        return error_response(str(e), 500)


@bp.route('/api/principles/<int:principle_id>/toggle', methods=['PATCH'])
def toggle_principle(principle_id: int) -> tuple:
    try:
        p = db_session.query(UserPrinciple).get(principle_id)
        if not p:
            return error_response('原则不存在', 404)
        p.is_active = not p.is_active
        db_session.commit()
        return success_response(principle=p.to_dict())
    except Exception as e:
        db_session.rollback()
        logger.error(f"toggle_principle error: {e}")
        return error_response(str(e), 500)
