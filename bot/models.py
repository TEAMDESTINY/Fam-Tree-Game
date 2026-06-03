"""SQLAlchemy ORM models for Fam-Tree-Game database."""

from datetime import date, datetime
from typing import Any, List, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    def __getitem__(self, key: str):
        return getattr(self, key)

    def get(self, key: str, default=None):
        return getattr(self, key, default)


# ============================================================
# CORE USER TABLE
# ============================================================


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[Optional[str]] = mapped_column(String(255))
    first_name: Mapped[Optional[str]] = mapped_column(String(255))
    profile_pic_file_id: Mapped[Optional[str]] = mapped_column(String(255))
    profile_pic_b64: Mapped[Optional[str]] = mapped_column(Text)
    gender: Mapped[Optional[str]] = mapped_column(String(20))
    hearts: Mapped[int] = mapped_column(Integer, server_default=text("3"))
    # If this account has been /transferaccount'd onto another user, this
    # column points at the new user_id. Funerals and other historical lookups
    # can still see the row.
    transferred: Mapped[Optional[int]] = mapped_column(BigInteger)
    last_updated: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


# ============================================================
# CRIME & COMBAT TABLES
# ============================================================


class CrimeLog(Base):
    __tablename__ = "crime_log"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    criminal_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id")
    )
    crime_type: Mapped[str] = mapped_column(String(50))
    victim_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("users.user_id")
    )
    is_solved: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false")
    )
    solved_by: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("users.user_id")
    )
    amount: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


class Jail(Base):
    __tablename__ = "jail"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id"), unique=True
    )
    jailed_by: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id")
    )
    reason: Mapped[str] = mapped_column(Text, server_default=text("''"))
    jailed_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    release_at: Mapped[datetime] = mapped_column(DateTime)


class DailyAction(Base):
    __tablename__ = "daily_actions"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id")
    )
    action_type: Mapped[str] = mapped_column(String(50))
    target_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("users.user_id")
    )
    action_date: Mapped[date] = mapped_column(
        Date, server_default=text("CURRENT_DATE")
    )
    count: Mapped[int] = mapped_column(Integer, server_default=text("1"))

    __table_args__ = (
        UniqueConstraint("user_id", "action_type", "target_id", "action_date"),
    )


# ============================================================
# FAMILY TABLES
# ============================================================


class FamilyRelationship(Base):
    __tablename__ = "family_relationships"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    parent_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id")
    )
    child_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id")
    )
    primary_adopter_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("users.user_id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("parent_id", "child_id"),
        CheckConstraint("parent_id != child_id"),
        Index("idx_family_parent", "parent_id"),
        Index("idx_family_child", "child_id"),
    )


class Marriage(Base):
    __tablename__ = "marriages"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    user1_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id")
    )
    user2_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "user1_id <> user2_id", name="marriages_users_distinct"
        ),
        UniqueConstraint("user1_id", "user2_id"),
        Index("idx_marriages_user1", "user1_id"),
        Index("idx_marriages_user2", "user2_id"),
    )


class Sibling(Base):
    __tablename__ = "siblings"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    user1_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id")
    )
    user2_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("user1_id <> user2_id", name="siblings_users_distinct"),
        UniqueConstraint("user1_id", "user2_id"),
        Index("idx_siblings_user1", "user1_id"),
        Index("idx_siblings_user2", "user2_id"),
    )


class PendingRequest(Base):
    __tablename__ = "pending_requests"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    request_type: Mapped[str] = mapped_column(String(20))
    requester_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id")
    )
    target_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id")
    )
    chat_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=text("(CURRENT_TIMESTAMP + INTERVAL '24 hours')"),
    )

    __table_args__ = (
        UniqueConstraint("request_type", "requester_id", "target_id"),
        Index("idx_pending_target", "target_id"),
    )


# ============================================================
# FRIENDSHIP TABLES
# ============================================================


class Friendship(Base):
    __tablename__ = "friendships"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    user1_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id")
    )
    user2_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "user1_id <> user2_id", name="friendships_users_distinct"
        ),
        UniqueConstraint("user1_id", "user2_id"),
        Index("idx_friendships_user1", "user1_id"),
        Index("idx_friendships_user2", "user2_id"),
    )


class FriendRequest(Base):
    __tablename__ = "friend_requests"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    requester_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id")
    )
    target_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    __table_args__ = (UniqueConstraint("requester_id", "target_id"),)


class FriendRating(Base):
    __tablename__ = "friend_ratings"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    rater_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id")
    )
    rated_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id")
    )
    rating: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("rating >= 1 AND rating <= 5"),
        UniqueConstraint("rater_id", "rated_id"),
    )


class FriendLink(Base):
    __tablename__ = "friend_links"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id")
    )
    link_code: Mapped[str] = mapped_column(String(32), unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


# ============================================================
# FINANCIAL TABLES
# ============================================================


class Wallet(Base):
    __tablename__ = "wallets"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id"), primary_key=True
    )
    balance: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    total_earned: Mapped[int] = mapped_column(
        BigInteger, server_default=text("0")
    )
    last_updated: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


class BankAccount(Base):
    __tablename__ = "bank_accounts"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id"), primary_key=True
    )
    balance: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    last_updated: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id")
    )
    amount: Mapped[int] = mapped_column(BigInteger)
    reason: Mapped[Optional[str]] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    __table_args__ = (Index("idx_transactions_user", "user_id"),)


# ============================================================
# MISC / ADMIN TABLES
# ============================================================


class FeedbackChat(Base):
    __tablename__ = "feedback_chats"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    chat_name: Mapped[Optional[str]] = mapped_column(String(255))
    added_by: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("users.user_id")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


class MarriageQuote(Base):
    __tablename__ = "marriage_quotes"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    quote: Mapped[str] = mapped_column(Text)
    is_remarriage: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false")
    )


class BlockedUser(Base):
    __tablename__ = "blocked_users"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    blocked_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


# ============================================================
# DAILY REWARD & GEM TABLES
# ============================================================


class DailyReward(Base):
    __tablename__ = "daily_rewards"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id"), primary_key=True
    )
    last_claim_date: Mapped[Optional[date]] = mapped_column(Date)
    current_gem: Mapped[Optional[str]] = mapped_column(String(20))
    streak: Mapped[int] = mapped_column(Integer, server_default=text("0"))


class GemFuseRequest(Base):
    __tablename__ = "gem_fuse_requests"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    requester_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id")
    )
    target_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id")
    )
    gem_type: Mapped[str] = mapped_column(String(20))
    chat_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=text("(CURRENT_TIMESTAMP + INTERVAL '1 hour')"),
    )

    __table_args__ = (UniqueConstraint("requester_id", "target_id"),)


# ============================================================
# GAMBLING TABLES
# ============================================================


class RippleGame(Base):
    __tablename__ = "ripple_games"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id")
    )
    bet_amount: Mapped[int] = mapped_column(BigInteger)
    current_prize: Mapped[int] = mapped_column(BigInteger)
    level: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    history: Mapped[str] = mapped_column(Text, server_default=text("''"))
    snake_positions: Mapped[str] = mapped_column(
        Text, server_default=text("''")
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, server_default=text("true")
    )
    chat_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    message_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


class RbetGame(Base):
    __tablename__ = "rbet_games"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id")
    )
    bet_amount: Mapped[int] = mapped_column(BigInteger)
    current_prize: Mapped[int] = mapped_column(BigInteger)
    level: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    is_active: Mapped[bool] = mapped_column(
        Boolean, server_default=text("true")
    )
    chat_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    message_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


class Lottery(Base):
    __tablename__ = "lotteries"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    host_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id")
    )
    stake_amount: Mapped[int] = mapped_column(BigInteger)
    participants: Mapped[List[int]] = mapped_column(
        ARRAY(BigInteger), server_default=text("'{}'")
    )
    winner_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    is_active: Mapped[bool] = mapped_column(
        Boolean, server_default=text("true")
    )
    chat_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    message_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


class GamblingStat(Base):
    __tablename__ = "gambling_stats"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id"), primary_key=True
    )
    game_type: Mapped[str] = mapped_column(String(20), primary_key=True)
    total_wagered: Mapped[int] = mapped_column(
        BigInteger, server_default=text("0")
    )
    total_won: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    total_lost: Mapped[int] = mapped_column(
        BigInteger, server_default=text("0")
    )
    biggest_win: Mapped[int] = mapped_column(
        BigInteger, server_default=text("0")
    )
    biggest_loss: Mapped[int] = mapped_column(
        BigInteger, server_default=text("0")
    )


# ============================================================
# FACTORY TABLES
# ============================================================


class Factory(Base):
    __tablename__ = "factories"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    owner_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id")
    )
    name: Mapped[str] = mapped_column(
        String(50), server_default=text("'My Factory'")
    )
    capacity: Mapped[int] = mapped_column(Integer, server_default=text("3"))
    total_earnings: Mapped[int] = mapped_column(
        BigInteger, server_default=text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    __table_args__ = (UniqueConstraint("owner_id"),)


class Worker(Base):
    __tablename__ = "workers"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id")
    )
    name: Mapped[str] = mapped_column(String(50))
    xp: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    __table_args__ = (UniqueConstraint("user_id"),)


class WorkerAssignment(Base):
    __tablename__ = "worker_assignments"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    worker_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("workers.id", ondelete="CASCADE")
    )
    factory_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("factories.id", ondelete="CASCADE")
    )
    fatigue: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    is_working: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false")
    )
    work_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_work_ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    total_shifts: Mapped[int] = mapped_column(Integer, server_default=text("0"))

    __table_args__ = (
        UniqueConstraint("worker_id", "factory_id"),
        Index("idx_worker_assignments_factory", "factory_id"),
    )


class FuneralDonation(Base):
    """A single donation to a deleted account's funeral.

    Many users can donate to the same deceased account, but each donor can
    donate to a given deceased only once (unique (donor, deceased)).
    """

    __tablename__ = "funerals_history"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    donor_user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.user_id", ondelete="CASCADE"),
    )
    deceased_user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.user_id", ondelete="CASCADE"),
    )
    amount: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("donor_user_id", "deceased_user_id"),
        Index("idx_funerals_deceased", "deceased_user_id"),
        Index("idx_funerals_donor", "donor_user_id"),
    )


class TransferBlock(Base):
    """A user can block another user from sending them money transfers."""

    __tablename__ = "transfer_blocks"

    owner_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.user_id", ondelete="CASCADE"),
        primary_key=True,
    )
    blocked_user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.user_id", ondelete="CASCADE"),
        primary_key=True,
    )
    blocked_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


class FactoryHireBlock(Base):
    __tablename__ = "factory_hire_blocks"

    factory_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("factories.id", ondelete="CASCADE"),
        primary_key=True,
    )
    blocked_worker_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("workers.id", ondelete="CASCADE"),
        primary_key=True,
    )
    blocked_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


# ============================================================
# GARDEN TABLES
# ============================================================


class Garden(Base):
    __tablename__ = "gardens"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    owner_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id")
    )
    size: Mapped[int] = mapped_column(Integer, server_default=text("3"))
    total_harvests: Mapped[int] = mapped_column(
        BigInteger, server_default=text("0")
    )
    last_fertilized_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    auto_harvest: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false")
    )
    notify_chat_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    __table_args__ = (UniqueConstraint("owner_id"),)


class GardenPlot(Base):
    __tablename__ = "garden_plots"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    garden_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("gardens.id", ondelete="CASCADE")
    )
    position: Mapped[int] = mapped_column(Integer)
    crop_type: Mapped[Optional[str]] = mapped_column(String(30))
    planted_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    is_ready: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false")
    )

    __table_args__ = (UniqueConstraint("garden_id", "position"),)


# ============================================================
# INVENTORY & MARKETPLACE TABLES
# ============================================================


class Inventory(Base):
    __tablename__ = "inventory"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id")
    )
    item_type: Mapped[str] = mapped_column(String(20))
    item_name: Mapped[str] = mapped_column(String(30))
    quantity: Mapped[int] = mapped_column(Integer, server_default=text("0"))

    __table_args__ = (
        UniqueConstraint("user_id", "item_type", "item_name"),
        Index("idx_inventory_user", "user_id"),
    )


class GiftInventory(Base):
    __tablename__ = "gift_inventory"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id")
    )
    item_type: Mapped[str] = mapped_column(String(20))
    item_name: Mapped[str] = mapped_column(String(30))
    quantity: Mapped[int] = mapped_column(Integer, server_default=text("0"))

    __table_args__ = (
        UniqueConstraint("user_id", "item_type", "item_name"),
        Index("idx_gift_inventory_user", "user_id"),
    )


class UserMachine(Base):
    __tablename__ = "user_machines"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id")
    )
    machine_type: Mapped[str] = mapped_column(String(30))
    purchased_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    __table_args__ = (UniqueConstraint("user_id", "machine_type"),)


class MarketplaceListing(Base):
    __tablename__ = "marketplace_listings"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    seller_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id")
    )
    item_type: Mapped[str] = mapped_column(String(20))
    item_name: Mapped[str] = mapped_column(String(30))
    quantity: Mapped[int] = mapped_column(Integer)
    price_each: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    __table_args__ = (
        Index("idx_marketplace_seller", "seller_id"),
        Index("idx_marketplace_item", "item_name"),
    )


class FertilizeCooldown(Base):
    __tablename__ = "fertilize_cooldowns"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    fertilizer_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id")
    )
    target_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id")
    )
    last_fertilized_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    __table_args__ = (UniqueConstraint("fertilizer_id", "target_id"),)


class FertilizeLog(Base):
    __tablename__ = "fertilize_log"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    fertilizer_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id")
    )
    target_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    __table_args__ = (
        Index("idx_fertilize_log_fertilizer", "fertilizer_id"),
        Index("idx_fertilize_log_target", "target_id"),
        Index("idx_fertilize_log_created", "created_at"),
    )


class FertilizeBan(Base):
    __tablename__ = "fertilize_bans"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id"), primary_key=True
    )
    banned_until: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


class FertilizeBanLog(Base):
    __tablename__ = "fertilize_ban_log"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id")
    )
    banned_until: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    fertilize_count: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(
        String(20), server_default=text("'auto'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


class FertilizeReceiveBan(Base):
    __tablename__ = "fertilize_receive_bans"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id"), primary_key=True
    )
    banned_until: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


class FertilizeReceiveBanLog(Base):
    __tablename__ = "fertilize_receive_ban_log"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id")
    )
    banned_until: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    fertilize_count: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(
        String(20), server_default=text("'auto'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


class HireRequest(Base):
    __tablename__ = "hire_requests"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    factory_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("factories.id", ondelete="CASCADE")
    )
    worker_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id")
    )
    requester_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id")
    )
    chat_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    __table_args__ = (UniqueConstraint("factory_id", "worker_user_id"),)


class HireBlock(Base):
    __tablename__ = "hire_blocks"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id"), primary_key=True
    )
    blocked_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id"), primary_key=True
    )
    blocked_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


# ============================================================
# SONAR GAME TABLE
# ============================================================


class SonarGame(Base):
    __tablename__ = "sonar_games"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id")
    )
    bet_amount: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    grid_size: Mapped[int] = mapped_column(Integer, server_default=text("10"))
    chest_positions: Mapped[List[int]] = mapped_column(ARRAY(Integer))
    guesses: Mapped[Any] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    found_positions: Mapped[List[int]] = mapped_column(
        ARRAY(Integer), server_default=text("'{}'")
    )
    revealed_cells: Mapped[List[int]] = mapped_column(
        ARRAY(Integer), server_default=text("'{}'")
    )
    chests_found: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    total_guesses: Mapped[int] = mapped_column(
        Integer, server_default=text("0")
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, server_default=text("true")
    )
    chat_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


# ============================================================
# FISHING TABLES
# ============================================================


class FishingInventory(Base):
    __tablename__ = "fishing_inventory"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id"), primary_key=True
    )
    fish_type: Mapped[str] = mapped_column(String(30), primary_key=True)
    quantity: Mapped[int] = mapped_column(Integer, server_default=text("0"))


class FishingStat(Base):
    __tablename__ = "fishing_stats"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id"), primary_key=True
    )
    bait_count: Mapped[int] = mapped_column(Integer, server_default=text("5"))
    total_caught: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    biggest_catch: Mapped[Optional[str]] = mapped_column(String(30))
    total_earned: Mapped[int] = mapped_column(
        BigInteger, server_default=text("0")
    )
    daily_fish_count: Mapped[int] = mapped_column(
        Integer, server_default=text("0")
    )
    daily_fish_date: Mapped[Optional[date]] = mapped_column(Date)


# ============================================================
# JOBS TABLES
# ============================================================


class WorkCooldown(Base):
    __tablename__ = "work_cooldowns"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id"), primary_key=True
    )
    last_work_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class Job(Base):
    __tablename__ = "jobs"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id"), primary_key=True
    )
    job_type: Mapped[str] = mapped_column(String(20))
    job_xp: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    job_level: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    stats: Mapped[Any] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


class JobSkill(Base):
    __tablename__ = "job_skills"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id"), primary_key=True
    )
    job_type: Mapped[str] = mapped_column(String(20), primary_key=True)
    job_xp: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    job_level: Mapped[int] = mapped_column(Integer, server_default=text("1"))


# ============================================================
# ACHIEVEMENTS TABLE
# ============================================================


class Achievement(Base):
    __tablename__ = "achievements"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id"), primary_key=True
    )
    achievement_key: Mapped[str] = mapped_column(String(50), primary_key=True)
    unlocked_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


# ============================================================
# 4-PIC GAME TABLE
# ============================================================


class FourPicGame(Base):
    __tablename__ = "four_pic_game"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    word: Mapped[str] = mapped_column(String(100))
    category: Mapped[str] = mapped_column(String(100))
    hint_message: Mapped[str] = mapped_column(Text)
    is_category_hint_given: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false")
    )
    is_hint_message_given: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false")
    )
    revealed_letters: Mapped[Any] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    photo_b64: Mapped[str] = mapped_column(Text)


# ============================================================
# NATION GAME TABLE
# ============================================================


class NationGame(Base):
    __tablename__ = "nation_game"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    nation_name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    photo_b64: Mapped[Optional[str]] = mapped_column(Text)


# ============================================================
# GANG TABLES  (migration 006)
# ============================================================


class Gang(Base):
    __tablename__ = "gangs"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    owner_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id")
    )
    name: Mapped[str] = mapped_column(String(100), unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


class GangMember(Base):
    __tablename__ = "gang_members"

    gang_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("gangs.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id"), primary_key=True
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    last_left_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    __table_args__ = (
        Index("idx_gang_members_user", "user_id"),
        Index("idx_gang_members_gang", "gang_id"),
    )


class GangWarLog(Base):
    __tablename__ = "gang_war_log"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    attacker_gang_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("gangs.id")
    )
    target_gang_id: Mapped[int] = mapped_column(Integer, ForeignKey("gangs.id"))
    attacker_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id")
    )
    target_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id")
    )
    result: Mapped[str] = mapped_column(String(20))
    hearts_lost: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    reward_amount: Mapped[int] = mapped_column(
        BigInteger, server_default=text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    __table_args__ = (
        Index("idx_gang_war_log_attacker", "attacker_id"),
        Index("idx_gang_war_log_target", "target_id"),
    )


class GangImmunity(Base):
    __tablename__ = "gang_immunity"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    gang_id: Mapped[int] = mapped_column(Integer, ForeignKey("gangs.id"))
    immune_from_gang_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("gangs.id")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime)

    __table_args__ = (
        UniqueConstraint("gang_id", "immune_from_gang_id"),
        Index("idx_gang_immunity_gang", "gang_id"),
    )


class UserSecurity(Base):
    __tablename__ = "user_security"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id"), primary_key=True
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false")
    )
    purchased_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    broken_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class HeistLog(Base):
    __tablename__ = "heist_log"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id")
    )
    target_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id")
    )
    result: Mapped[str] = mapped_column(String(20))
    amount: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    __table_args__ = (
        Index("idx_heist_log_user", "user_id"),
        Index("idx_heist_log_target", "target_id"),
        Index("idx_heist_log_created", "created_at"),
    )


# ============================================================
# HEIST VAULTS TABLE  (migration 007)
# ============================================================


class HeistVault(Base):
    __tablename__ = "heist_vaults"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    attacker_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id")
    )
    target_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id")
    )
    correct_vault: Mapped[int] = mapped_column(Integer)
    num_vaults: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    __table_args__ = (UniqueConstraint("attacker_id", "target_id"),)


# ============================================================
# UNLOCKED CRAFTS TABLE  (migration 008)
# ============================================================


class UnlockedCraft(Base):
    __tablename__ = "unlocked_crafts"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id"), primary_key=True
    )
    crafts: Mapped[Any] = mapped_column(
        JSONB,
        server_default=text(
            """'["air", "earth", "fire", "water", "monster", "good", "evil", "immortality"]'::jsonb"""
        ),
    )
    combos: Mapped[Any] = mapped_column(
        JSONB, server_default=text("""'[]'::jsonb""")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


# ============================================================
# CHATS TABLE (migration 009)
# ============================================================


class Chat(Base):
    __tablename__ = "chats"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    chat_type: Mapped[Optional[str]] = mapped_column(
        "type", String, nullable=True
    )
    title: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    username: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    photo: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    dc_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    members_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    is_forum: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    is_members_hidden: Mapped[Optional[bool]] = mapped_column(
        Boolean, nullable=True
    )
    is_restricted: Mapped[Optional[bool]] = mapped_column(
        Boolean, nullable=True
    )
    restriction_reason: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class AnimalPen(Base):
    __tablename__ = "animal_pens"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.user_id")
    )
    pen_type: Mapped[str] = mapped_column(String(30))
    level: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("user_id", "pen_type"),
        Index("idx_animal_pens_user", "user_id"),
    )


class Animal(Base):
    __tablename__ = "animals"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    pen_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("animal_pens.id", ondelete="CASCADE")
    )
    animal_type: Mapped[str] = mapped_column(String(20))
    last_fed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True
    )
    ready_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True
    )
    is_ready: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    __table_args__ = (Index("idx_animals_pen", "pen_id"),)


class Pet(Base):
    __tablename__ = "pets"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    user_id: Mapped[int] = mapped_column(BigInteger)
    pet_type: Mapped[str] = mapped_column(String(30))
    pet_name: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    level: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    happiness: Mapped[int] = mapped_column(Integer, server_default=text("100"))
    happiness_updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    __table_args__ = (UniqueConstraint("user_id", "pet_type"),)


class PetChangeHistory(Base):
    __tablename__ = "pet_change_history"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    user_id: Mapped[int] = mapped_column(BigInteger)
    old_pet_type: Mapped[str] = mapped_column(String(30))
    old_level: Mapped[int] = mapped_column(Integer)
    new_pet_type: Mapped[str] = mapped_column(String(30))
    changed_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
