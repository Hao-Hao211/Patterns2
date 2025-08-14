import asyncio
import asyncpg
import os
from dotenv import load_dotenv

load_dotenv()

async def init_database():
    """初始化数据库表"""
    DATABASE_URL = os.getenv("DATABASE_URL")
    if not DATABASE_URL:
        print("错误: 未设置DATABASE_URL环境变量")
        return
    
    conn = await asyncpg.connect(DATABASE_URL)
    
    try:
        # 读取并执行schema.sql
        with open("../scripts/schema.sql", "r", encoding="utf-8") as f:
            schema_sql = f.read()
        
        await conn.execute(schema_sql)
        print("数据库表创建成功!")
        
    except Exception as e:
        print(f"数据库初始化失败: {e}")
    finally:
        await conn.close()

if __name__ == "__main__":
    asyncio.run(init_database())
