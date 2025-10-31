from sqlalchemy import (create_engine, Column, Integer, String, Text, DateTime, UniqueConstraint,
                        JSON)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime, timezone
from sqlalchemy import ForeignKey, Boolean

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
    category = Column(JSON)  # 列表
    link = Column(Text)
    pdf_link = Column(Text)
    comment = Column(Text)
    added_time = Column(DateTime, default=datetime.now(timezone.utc))
    tags = Column(JSON)  # AI生成的标签，列表
    description = Column(Text)  # AI生成的简述
    translation = Column(Text, default="")  # AI生成的摘要翻译
    __table_args__ = (UniqueConstraint("arxiv_id", name="_arxiv_id_uc"), )


class UserConfig(Base):
    __tablename__ = "user_config"

    user_id = Column(Integer, primary_key=True)
    platform = Column(String(32), nullable=False, default="telegram")  # 用户来源
    description = Column(Text)
    search_queries = Column(JSON, nullable=True)
    # 存储用户的检索式，使用 JSON 格式，例如：# [{"query": "cat:cs.CL", "max_results": 10}, ...]
    since_days = Column(Integer, default=7)
    last_check = Column(String(64))
    created_at = Column(DateTime, default=datetime.now(timezone.utc))  # 用户创建时间


class PaperUserNotify(Base):
    __tablename__ = "paper_user_notify"

    id = Column(Integer, primary_key=True, autoincrement=True)
    arxiv_id = Column(String(64), ForeignKey("papers.arxiv_id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("user_config.user_id", ondelete="CASCADE"), nullable=False)
    sent_time = Column(DateTime, default=datetime.now(timezone.utc))

    __table_args__ = (UniqueConstraint("arxiv_id", "user_id", name="_paper_user_uc"), )


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

    def get_paper_data(self, arxiv_id: str) -> dict | None:
        """
        根据 arxiv_id 返回数据库里的论文原始数据字典。
        """
        with self.Session() as session:
            paper = session.query(Paper).filter_by(arxiv_id=arxiv_id).first()
            if not paper:
                return None

            return {
                "arxiv_id": paper.arxiv_id,
                "title": paper.title,
                "authors": paper.authors or [],
                "summary": paper.summary or "",
                "published": paper.published or "",
                "updated": paper.updated or "",
                "category": paper.category or [],
                "link": paper.link or "",
                "pdf_link": paper.pdf_link or "",
                "comment": paper.comment or "",
                "tags": paper.tags or [],
                "description": paper.description or "",
                "translation": paper.translation or ""
            }

    def search_papers(self, keyword: str):
        with self.Session() as session:
            q = f"%{keyword}%"
            return session.query(Paper).filter((Paper.title.like(q))
                                               | (Paper.summary.like(q))).all()

    def paper_exists(self, arxiv_id: str) -> bool:
        with self.Session() as session:
            return session.query(Paper).filter_by(arxiv_id=arxiv_id).first() is not None

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

    def get_users_by_platform(self, platform: str):
        """获取指定平台的所有用户配置"""
        with self.Session() as session:
            users = session.query(UserConfig).filter(UserConfig.platform == platform).all()
            for user in users:
                if user.search_queries:
                    keywords_list = [
                        sq.get('query', '') for sq in user.search_queries if sq.get('query')
                    ]
                    user.keywords = ' '.join(keywords_list)
                else:
                    user.keywords = None
            return users

    def get_telegram_users(self):
        """获取所有 Telegram 用户配置"""
        return self.get_users_by_platform("telegram")

    def get_matrix_users(self):
        """获取所有 Matrix 房间配置"""
        return self.get_users_by_platform("matrix")

    def delete_user(self, user_id: int) -> bool:
        with self.Session() as session:
            user = session.query(UserConfig).filter_by(user_id=user_id).first()
            if not user:
                return False
            session.delete(user)
            session.commit()
            return True

    def is_sended(self, arxiv_id: str, user_id: int) -> bool:
        """判断该论文是否已发送给该用户"""
        with self.Session() as session:
            exists = session.query(PaperUserNotify).filter_by(arxiv_id=arxiv_id,
                                                              user_id=user_id).first()
            return exists is not None

    def sended(self, arxiv_id: str, user_id: int):
        """记录该论文已发送给用户"""
        with self.Session() as session:
            if self.is_sended(arxiv_id, user_id):
                return False  # 已存在
            record = PaperUserNotify(arxiv_id=arxiv_id, user_id=user_id)
            session.add(record)
            session.commit()
            return True

    def get_sended_users(self, arxiv_id: str) -> list[int]:
        """获取已经收到该论文的用户ID列表"""
        with self.Session() as session:
            records = session.query(PaperUserNotify.user_id).filter_by(arxiv_id=arxiv_id).all()
            return [r.user_id for r in records]
