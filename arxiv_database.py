from sqlalchemy import (create_engine, Column, Integer, String, Text, DateTime, UniqueConstraint,
                        JSON)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime

Base = declarative_base()


# =========================
#  数据表定义
# =========================
class Paper(Base):
    __tablename__ = "papers"

    arxiv_id = Column(String(64), primary_key=True, unique=True, nullable=False)
    title = Column(Text, nullable=False)
    authors = Column(JSON)  # 列表
    summary = Column(Text)
    published = Column(String(64))
    updated = Column(String(64))
    link = Column(Text)
    category = Column(JSON)  # 列表
    added_time = Column(DateTime, default=datetime.utcnow)
    user_notify = Column(JSON)  # 列表，存储已经通知的用户ID
    tags = Column(JSON)  # AI生成的标签，列表
    description = Column(Text)  # AI生成的简述

    __table_args__ = (UniqueConstraint("arxiv_id", name="_arxiv_id_uc"), )
    description = Column(Text)  # AI生成的简述

    __table_args__ = (UniqueConstraint("arxiv_id", name="_arxiv_id_uc"), )


class UserConfig(Base):
    __tablename__ = "user_config"

    user_id = Column(Integer, primary_key=True)
    description = Column(Text)
    search_queries = Column(JSON, nullable=True)
    # 存储用户的检索式，使用 JSON 格式，例如：# [{"query": "cat:cs.CL", "max_results": 10}, ...]
    since_days = Column(Integer, default=7)
    schedule_time = Column(String(32))
    last_check = Column(String(64))
    created_at = Column(DateTime, default=datetime.utcnow)  # 用户创建时间


# =========================
#  数据库管理类
# =========================
class DatabaseManager:

    def __init__(self, db_config: dict):
        self.config = db_config
        self.engine = None
        self.Session = None
        self._connect()

    def _connect(self):
        user = self.config.get("user")
        password = self.config.get("password")
        host = self.config.get("host", "localhost")
        port = self.config.get("port", 5432)
        name = self.config.get("name", "arxiv_bot")

        db_url = f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{name}"
        self.engine = create_engine(db_url, echo=False, future=True)
        self.Session = sessionmaker(bind=self.engine)
        Base.metadata.create_all(self.engine)

    # =========================
    #  论文操作
    # =========================

    def insert_paper(self, paper_data: dict) -> bool:
        with self.Session() as session:
            try:
                new_paper = Paper(**paper_data)
                session.add(new_paper)
                session.commit()
                return True
            except Exception as e:
                session.rollback()  # 回滚事务
                print(f"Error inserting paper: {e}")
                return False

    def delete_paper(self, arxiv_id: str) -> bool:
        """根据 arxiv_id 删除论文"""
        with self.Session() as session:
            paper = session.query(Paper).filter_by(arxiv_id=arxiv_id).first()
            if not paper:
                return False
            session.delete(paper)
            session.commit()
            return True

    def get_recent_papers(self, limit: int = 10):
        with self.Session() as session:
            return session.query(Paper).order_by(Paper.added_time.desc()).limit(limit).all()

    def search_papers(self, keyword: str):
        with self.Session() as session:
            q = f"%{keyword}%"
            return session.query(Paper).filter((Paper.title.like(q))
                                               | (Paper.summary.like(q))).all()

    def paper_exists(self, arxiv_id: str) -> bool:
        with self.Session() as session:
            return session.query(Paper).filter_by(arxiv_id=arxiv_id).first() is not None

    def get_user_notify(self, arxiv_id: str) -> list:
        """获取已通知的用户ID列表"""
        with self.Session() as session:
            paper = session.query(Paper).filter_by(arxiv_id=arxiv_id).first()
            return paper.user_notify if paper and paper.user_notify else []

    def get_user_notify(self, arxiv_id: str) -> list:
        """根据 arxiv_id 获取已通知用户列表"""
        with self.Session() as session:
            paper = session.query(Paper).filter(Paper.arxiv_id == arxiv_id).first()
            if paper and paper.user_notify:
                return paper.user_notify
            return []

    def update_user_notify(self, arxiv_id: str, user_id: int):
        """将新的用户ID加入user_notify列表"""
        with self.Session() as session:
            paper = session.query(Paper).filter(Paper.arxiv_id == arxiv_id).first()
            if not paper:
                return False

            notified = paper.user_notify or []
            if user_id not in notified:
                notified.append(user_id)
                paper.user_notify = notified
                session.commit()
                return True
            return False

    # =========================
    #  用户配置操作
    # =========================
    def insert_or_update_user(self, user_id: int, config: dict):
        with self.Session() as session:
            user = session.query(UserConfig).filter_by(user_id=user_id).first()
            if user:
                for k, v in config.items():
                    setattr(user, k, v)
            else:
                user = UserConfig(user_id=user_id, **config)
                session.add(user)
            session.commit()

    def get_user_config(self, user_id: int):
        with self.Session() as session:
            user = session.query(UserConfig).filter_by(user_id=user_id).first()
            if user and user.search_queries:
                # 为向后兼容，从 search_queries 生成 keywords 属性
                keywords_list = [
                    sq.get('query', '') for sq in user.search_queries if sq.get('query')
                ]
                user.keywords = ' '.join(keywords_list)
            return user

    def get_all_users(self):
        """返回数据库中所有用户配置，用于后台循环推送"""
        with self.Session() as session:
            users = session.query(UserConfig).all()
            # 为向后兼容，为每个用户添加 keywords 属性
            for user in users:
                if user.search_queries:
                    keywords_list = [
                        sq.get('query', '') for sq in user.search_queries if sq.get('query')
                    ]
                    user.keywords = ' '.join(keywords_list)
                else:
                    user.keywords = None
            return users

    def delete_user(self, user_id: int) -> bool:
        with self.Session() as session:
            user = session.query(UserConfig).filter_by(user_id=user_id).first()
            if not user:
                return False
            session.delete(user)
            session.commit()
            return True
