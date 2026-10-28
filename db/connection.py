from dotenv import load_dotenv
import os
from sqlalchemy import create_engine,text

load_dotenv()

DATABASE_URL = (
    f"postgresql+psycopg://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}"
    f"@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}"
)

engine = create_engine(DATABASE_URL,pool_pre_ping=True);

def test_connection():
    with engine.connect() as conn:
        version = conn.execute(text("SELECT version();")).scalar()
        print("Connected :",version)

if __name__=="__main__":
    test_connection()