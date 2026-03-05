#!/usr/bin/env python3
"""
一次性迁移脚本：为每个用户创建独立的 SQLite 数据库文件。

- 将现有 stock_trading.db 复制为 stock_trading_admin.db（保留全部数据）
- 为其余用户创建空数据库并建表 + 运行迁移

用法：
    cd backend && python -m scripts.init_user_dbs
"""
import os
import sys
import shutil

# Ensure backend/ is on sys.path
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BACKEND_DIR)

from dotenv import load_dotenv
load_dotenv(os.path.join(_BACKEND_DIR, '.env'), override=True)


def main():
    from app.config.database import (
        _DATA_DIR, _db_path_for_user, _parse_usernames, _get_engine,
    )
    from app.models.base import Base
    import app.models  # noqa: F401 — register all models

    # Migration functions
    from app.models.financial_data import migrate_financial_data_v3
    from migrations.add_market_currency import run as migrate_market_currency
    from migrations.add_data_source_column import run as migrate_data_source
    from migrations.fix_us_stock_currency import run as fix_us_currency
    from migrations.add_include_principles import run as migrate_include_principles

    migrations = [
        migrate_financial_data_v3,
        migrate_market_currency,
        migrate_data_source,
        fix_us_currency,
        migrate_include_principles,
    ]

    os.makedirs(_DATA_DIR, exist_ok=True)

    original_db = os.path.join(_DATA_DIR, 'stock_trading.db')
    admin_db = _db_path_for_user('admin')

    # Step 1: Copy original → admin (if admin DB doesn't exist yet)
    if os.path.exists(original_db) and not os.path.exists(admin_db):
        shutil.copy2(original_db, admin_db)
        print(f"✅ 复制 stock_trading.db → stock_trading_admin.db")
    elif os.path.exists(admin_db):
        print(f"⏭️  stock_trading_admin.db 已存在，跳过复制")
    else:
        print(f"⚠️  stock_trading.db 不存在，将为 admin 创建空库")

    # Step 2: For each user, create DB + tables + migrations
    usernames = _parse_usernames()
    print(f"\n用户列表: {usernames}\n")

    for username in usernames:
        db_path = _db_path_for_user(username)
        is_new = not os.path.exists(db_path)

        engine = _get_engine(username)

        if is_new and username != 'admin':
            # Create empty database with all tables
            Base.metadata.create_all(bind=engine)
            print(f"✅ [{username}] 创建空数据库: {db_path}")
        else:
            print(f"📂 [{username}] 数据库已存在: {db_path}")

        # Run migrations (idempotent, safe to re-run)
        for migrate_fn in migrations:
            try:
                migrate_fn(engine)
            except Exception as e:
                print(f"  ⚠️  [{username}] 迁移 {migrate_fn.__name__}: {e}")

        print(f"  ✅ [{username}] 迁移完成")

    # Step 3: Write default API keys to user_settings
    _seed_default_settings(usernames)

    print(f"\n🎉 所有用户数据库初始化完成！")
    print(f"   原始 stock_trading.db 已保留作为备份。")


def _seed_default_settings(usernames):
    """将默认 API Key 写入每个用户的 user_settings 表"""
    from app.config.database import create_user_session
    from app.models.user_setting import UserSetting

    # 从环境变量读取默认 key
    defaults = {
        'finnhub_api_key': os.getenv('FINNHUB_API_KEY', ''),
    }
    # 过滤掉空值和占位符
    defaults = {k: v for k, v in defaults.items() if v and not v.startswith('your_')}

    if not defaults:
        print("\n⏭️  无默认 API Key 需要写入（请在 .env 中配置）")
        return

    print(f"\n📝 写入默认 API Key: {list(defaults.keys())}")
    for username in usernames:
        s = create_user_session(username)
        try:
            for key, value in defaults.items():
                row = s.query(UserSetting).filter_by(key=key).first()
                if row:
                    row.value = value
                else:
                    s.add(UserSetting(key=key, value=value))
            s.commit()
            print(f"  ✅ [{username}] API Key 已写入")
        except Exception as e:
            print(f"  ⚠️  [{username}] 写入 API Key 失败: {e}")
        finally:
            s.close()


if __name__ == '__main__':
    main()
