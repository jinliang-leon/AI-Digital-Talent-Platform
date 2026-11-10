"""本机初始化入口；密码从交互或环境读取，不接受命令行明文参数。"""

import argparse
import getpass
import os

from sqlalchemy import select

from app.config import get_settings
from app.db import make_session_factory
from app.models import Permission, Role, User
from app.services.auth import ROLE_PERMISSIONS, hash_password


def seed_roles(session):
    for code, codes in ROLE_PERMISSIONS.items():
        role = session.scalar(select(Role).where(Role.code == code))
        if not role:
            role = Role(code=code)
            session.add(role)
        perms = []
        for item in sorted(codes):
            permission = session.scalar(select(Permission).where(Permission.code == item))
            if not permission:
                permission = Permission(code=item)
                session.add(permission)
                session.flush()
            perms.append(permission)
        role.permissions = perms
    session.flush()


def init_admin(username, password):
    factory, engine = make_session_factory(get_settings())
    try:
        with factory.begin() as session:
            seed_roles(session)
            if session.scalar(select(User).where(User.username == username)):
                print("该账号已存在，未修改其密码或权限。")
                return
            roles = list(session.scalars(select(Role)))
            session.add(User(username=username, password_hash=hash_password(password), roles=roles))
        print("管理员已创建，请使用本地保存的密码登录。")
    finally:
        engine.dispose()


def main():
    parser = argparse.ArgumentParser(description="AI数字化人才平台管理工具")
    sub = parser.add_subparsers(dest="command", required=True)
    admin = sub.add_parser("init-admin", help="创建管理员（同时具备 HR 权限）")
    admin.add_argument("--username", default="admin")
    args = parser.parse_args()
    if args.command == "init-admin":
        password = os.environ.get("INITIAL_ADMIN_PASSWORD") or getpass.getpass(
            "设置管理员密码（至少10字符）："
        )
        init_admin(args.username, password)


if __name__ == "__main__":
    main()
