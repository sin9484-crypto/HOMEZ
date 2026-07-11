from app.database.base import Base
from app.database.session import engine


def initialize_database():
    Base.metadata.create_all(bind=engine)