"""
Authentication module using Flask native session.
No external auth libraries required.

多用户配置方式（二选一）：
  1. 环境变量 AUTH_USERS（推荐）：逗号分隔的 user:pass 对
     AUTH_USERS=admin:stockadmin123,charlie:charlie123,father:father123
  2. 兼容旧版单用户环境变量 AUTH_USERNAME / AUTH_PASSWORD
"""
import os
import logging
from flask import Blueprint, request, session, redirect, url_for, render_template, jsonify

logger = logging.getLogger(__name__)

auth_bp = Blueprint('auth', __name__)

# Routes that don't require authentication
AUTH_WHITELIST = ('/login', '/static/', '/health')


def _load_users() -> dict:
    """
    加载用户表，返回 {username: password} 字典。
    优先读 AUTH_USERS 环境变量，格式：user1:pass1,user2:pass2,...
    若未设置则回退到旧版 AUTH_USERNAME/AUTH_PASSWORD。
    """
    users = {}

    auth_users_env = os.getenv('AUTH_USERS', '').strip()
    if auth_users_env:
        for pair in auth_users_env.split(','):
            pair = pair.strip()
            if ':' in pair:
                u, p = pair.split(':', 1)
                u, p = u.strip(), p.strip()
                if u and p:
                    users[u] = p

    # 兼容旧版单用户配置
    if not users:
        users[os.getenv('AUTH_USERNAME', 'admin')] = os.getenv('AUTH_PASSWORD', 'stockadmin123')

    return users


def login_required():
    """before_request hook that redirects unauthenticated users to login."""
    if request.path.startswith(AUTH_WHITELIST):
        return None
    if not session.get('authenticated'):
        if request.path.startswith('/api/'):
            return jsonify({'error': 'Authentication required'}), 401
        return redirect(url_for('auth.login', next=request.path))
    return None


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('authenticated'):
        return redirect(url_for('web.dashboard'))

    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        users = _load_users()

        if username in users and users[username] == password:
            session['authenticated'] = True
            session['username'] = username
            next_url = request.args.get('next') or request.form.get('next') or '/'

            # 将 API Key 持久化到用户的 SQLite 数据库
            api_key = request.form.get('api_key', '').strip()
            minimax_key = request.form.get('minimax_api_key', '').strip()
            if api_key:
                _save_setting(username, 'anthropic_api_key', api_key)
            if minimax_key:
                _save_setting(username, 'minimax_api_key', minimax_key)
            # 自动设置默认 AI 提供商
            if minimax_key and not api_key:
                _save_setting(username, 'ai_provider', 'minimax')

            logger.info(f"用户 {username} 登录成功")
            return redirect(next_url)
        else:
            error = '用户名或密码错误'
            logger.warning(f"登录失败: {username}")

    return render_template('login.html', error=error)


@auth_bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('auth.login'))


def _save_setting(username: str, key: str, value: str):
    """将设置持久化到用户的 SQLite 数据库"""
    try:
        from app.config.database import create_user_session
        from app.models.user_setting import UserSetting
        s = create_user_session(username)
        try:
            row = s.query(UserSetting).filter_by(key=key).first()
            if row:
                row.value = value
            else:
                s.add(UserSetting(key=key, value=value))
            s.commit()
        finally:
            s.close()
    except Exception as e:
        logger.warning(f"保存设置失败 [{username}/{key}]: {e}")


# 向后兼容
_save_api_key = lambda username, api_key: _save_setting(username, 'anthropic_api_key', api_key)
