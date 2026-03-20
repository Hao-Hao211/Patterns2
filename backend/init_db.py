import asyncio
import asyncpg
import os
from dotenv import load_dotenv

load_dotenv()

async def init_database():
    """Initialize database tables."""
    DATABASE_URL = os.getenv("DATABASE_URL")
    if not DATABASE_URL:
        print("Error: DATABASE_URL environment variable not set")
        return

    conn = await asyncpg.connect(DATABASE_URL)

    try:
        # Read and execute schema.sql
        with open("../scripts/schema.sql", "r", encoding="utf-8") as f:
            schema_sql = f.read()

        await conn.execute(schema_sql)
        print("Database tables created successfully!")

    except Exception as e:
        print(f"Database initialization failed: {e}")
    finally:
        await conn.close()

if __name__ == "__main__":
    asyncio.run(init_database())
