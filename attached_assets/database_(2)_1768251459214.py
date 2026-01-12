import os
from sqlalchemy import create_engine, Column, String, BigInteger, Float, Boolean, DateTime, ForeignKey, Text, Index
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime

DATABASE_URL = os.environ.get('DATABASE_URL', '')
if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable is required")
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=300,
    pool_size=5,
    max_overflow=10
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class ServerConfig(Base):
    __tablename__ = 'server_configs'
    
    guild_id = Column(BigInteger, primary_key=True)
    alert_channel_id = Column(BigInteger, nullable=True)
    volatility_channel_id = Column(BigInteger, nullable=True)
    sports_channel_id = Column(BigInteger, nullable=True)
    whale_channel_id = Column(BigInteger, nullable=True)
    fresh_wallet_channel_id = Column(BigInteger, nullable=True)
    tracked_wallet_channel_id = Column(BigInteger, nullable=True)
    top_trader_channel_id = Column(BigInteger, nullable=True)
    bonds_channel_id = Column(BigInteger, nullable=True)
    whale_threshold = Column(Float, default=10000.0)
    fresh_wallet_threshold = Column(Float, default=10000.0)
    sports_threshold = Column(Float, default=5000.0)
    volatility_threshold = Column(Float, default=20.0)
    volatility_window_minutes = Column(BigInteger, default=60)
    is_paused = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    tracked_wallets = relationship("TrackedWallet", back_populates="server", cascade="all, delete-orphan")


class TrackedWallet(Base):
    __tablename__ = 'tracked_wallets'
    
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    guild_id = Column(BigInteger, ForeignKey('server_configs.guild_id'), nullable=False)
    wallet_address = Column(String(42), nullable=False)
    label = Column(String(100), nullable=True)
    added_by = Column(BigInteger, nullable=True)
    added_at = Column(DateTime, default=datetime.utcnow)
    
    server = relationship("ServerConfig", back_populates="tracked_wallets")


class SeenTransaction(Base):
    __tablename__ = 'seen_transactions'
    
    tx_hash = Column(String(66), primary_key=True)
    seen_at = Column(DateTime, default=datetime.utcnow)


class WalletActivity(Base):
    __tablename__ = 'wallet_activity'
    
    wallet_address = Column(String(42), primary_key=True)
    first_seen = Column(DateTime, default=datetime.utcnow)
    transaction_count = Column(BigInteger, default=0)


class PriceSnapshot(Base):
    __tablename__ = 'price_snapshots'
    
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    condition_id = Column(String(100), nullable=False, index=True)
    title = Column(Text, nullable=True)
    slug = Column(String(200), nullable=True)
    yes_price = Column(Float, nullable=False)
    volume = Column(Float, default=0)
    captured_at = Column(DateTime, default=datetime.utcnow, index=True)


class VolatilityAlert(Base):
    __tablename__ = 'volatility_alerts'
    
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    condition_id = Column(String(100), nullable=False, index=True)
    alerted_at = Column(DateTime, default=datetime.utcnow)
    price_change = Column(Float, nullable=False)


# NEW TABLE: Maps short IDs to full market slugs for Telegram deep links
class MarketSlugMapping(Base):
    __tablename__ = 'market_slug_mappings'
    
    short_id = Column(String(16), primary_key=True)  # e.g., "m_a3f8c2b1"
    full_slug = Column(Text, nullable=False)          # Full Polymarket event slug
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Index for faster lookups by slug (to avoid duplicates)
    __table_args__ = (
        Index('ix_market_slug_mappings_full_slug', 'full_slug'),
    )


def init_db():
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_session():
    return SessionLocal()
