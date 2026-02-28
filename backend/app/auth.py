"""
Authentication module using Flask native session.
No external auth libraries required.
"""
import os
from flask import Blueprint, request, session, redirect, url_for, render_template, jsonify

auth_bp = Blueprint('auth', __name__)

# Routes that don't require authentication
AUTH_WHITELIST = ('/login', '/static/', '/health')


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
        username = request.form.get('username', '')
        password = request.form.get('password', '')

        expected_user = os.getenv('AUTH_USERNAME', 'admin')
        expected_pass = os.getenv('AUTH_PASSWORD', 'stockadmin123')

        if username == expected_user and password == expected_pass:
            session['authenticated'] = True
            session['username'] = username
            # 存储用户输入的 Claude API Key
            session['anthropic_api_key'] = request.form.get('api_key', '').strip()
            next_url = request.args.get('next') or request.form.get('next') or '/'
            return redirect(next_url)
        else:
            error = '用户名或密码错误'

    return render_template('login.html', error=error)


@auth_bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('auth.login'))
