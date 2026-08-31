"""Create or migrate the database schema without inserting demo user data."""

from database import initialize_database


if __name__ == "__main__":
    initialize_database()
    print("数据库结构已初始化。请通过注册接口创建用户。")
