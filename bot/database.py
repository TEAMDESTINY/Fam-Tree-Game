"""Database management with SQLAlchemy 2.x async ORM."""

import json
import re
import secrets
import time
from datetime import datetime
from typing import Any, List, Optional, Tuple

from sqlalchemy import (
    and_,
    delete,
    exists,
    func,
    insert,
    or_,
    select,
    text,
    union,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from bot.models import (
    Achievement,
    Animal,
    AnimalPen,
    BankAccount,
    Base,
    BlockedUser,
    CrimeLog,
    DailyReward,
    Factory,
    FactoryHireBlock,
    FuneralDonation,
    TransferBlock,
    FamilyRelationship,
    FeedbackChat,
    Chat,
    FertilizeBan,
    FertilizeBanLog,
    FertilizeCooldown,
    FertilizeLog,
    FertilizeReceiveBan,
    FertilizeReceiveBanLog,
    FishingInventory,
    FishingStat,
    FourPicGame,
    NationGame,
    FriendLink,
    FriendRating,
    FriendRequest,
    Friendship,
    Gang,
    GangImmunity,
    GangMember,
    GangWarLog,
    Garden,
    GardenPlot,
    GemFuseRequest,
    GiftInventory,
    HeistLog,
    HeistVault,
    HireBlock,
    HireRequest,
    Inventory,
    Jail,
    Job,
    JobSkill,
    MarketplaceListing,
    Marriage,
    MarriageQuote,
    PendingRequest,
    Pet,
    PetChangeHistory,
    Sibling,
    SonarGame,
    Transaction,
    User,
    UserMachine,
    UserSecurity,
    Wallet,
    WorkCooldown,
    Worker,
    WorkerAssignment,
)


class TTLCache:
    """
    Simple TTL (Time-To-Live) cache for database queries.

    # XXX: Caches expensive queries like family trees and friend circles.
    # XXX: Entries expire after ttl_seconds to ensure data freshness.
    """

    def __init__(self, ttl_seconds: int = 60):
        self._cache: dict[str, tuple[float, Any]] = {}
        self.ttl = ttl_seconds

    def get(self, key: str) -> Optional[Any]:
        """Get a cached value if it exists and hasn't expired."""
        if key in self._cache:
            timestamp, value = self._cache[key]
            if time.time() - timestamp < self.ttl:
                return value
            del self._cache[key]
        return None

    def set(self, key: str, value: Any):
        """Cache a value with current timestamp."""
        self._cache[key] = (time.time(), value)

    def invalidate(self, key: str):
        """Remove a specific key from cache."""
        self._cache.pop(key, None)

    def invalidate_prefix(self, prefix: str):
        """Remove all keys starting with prefix."""
        keys_to_remove = [k for k in self._cache if k.startswith(prefix)]
        for k in keys_to_remove:
            del self._cache[k]

    def clear(self):
        """Clear all cached entries."""
        self._cache.clear()


def _translate_asyncpg_query(
    query: str, args: tuple[Any, ...]
) -> tuple[str, dict[str, Any]]:
    """Translate asyncpg-style placeholders ($1, $2, ...) to SQLAlchemy params."""
    translated = re.sub(r"\$(\d+)", lambda m: f":p{m.group(1)}", query)
    # SQLAlchemy text() doesn't support :name::type shorthand reliably.
    translated = re.sub(
        r"(:p\d+)::([A-Za-z_][A-Za-z0-9_]*)",
        r"CAST(\1 AS \2)",
        translated,
    )
    params = {f"p{i}": arg for i, arg in enumerate(args, start=1)}
    return translated, params


class RowObject(dict):
    """Attribute-access row wrapper for non-ORM query results."""

    def __getattr__(self, key: str):
        try:
            return self[key]
        except KeyError as e:
            raise AttributeError(key) from e


class _DbQueryConnection:
    """Connection wrapper for direct SQLAlchemy text queries."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def fetch(self, query: str, *args):
        sql, params = _translate_asyncpg_query(query, args)
        result = await self._session.execute(text(sql), params)
        return _result_all(result)

    async def fetchrow(self, query: str, *args):
        sql, params = _translate_asyncpg_query(query, args)
        result = await self._session.execute(text(sql), params)
        return _result_one(result)

    async def fetchval(self, query: str, *args):
        row = await self.fetchrow(query, *args)
        if not row:
            return None
        return next(iter(row.values()))

    async def execute(self, query: str, *args):
        sql, params = _translate_asyncpg_query(query, args)
        await self._session.execute(text(sql), params)
        await self._session.commit()


def _unwrap_mapping_row(row):
    if row is None:
        return None
    if len(row) == 1:
        only_value = next(iter(row.values()))
        if isinstance(only_value, Base):
            return only_value
    return RowObject(row)


def _result_one(result):
    return _unwrap_mapping_row(result.mappings().fetchone())


def _result_all(result):
    rows = result.mappings().fetchall()
    return [_unwrap_mapping_row(row) for row in rows]


class _DatabaseProxy:
    """Proxy so `from bot.database import db` works before initialization."""

    def __init__(self):
        self._client: "Database | None" = None

    def set(self, database: "Database") -> None:
        self._client = database

    def get(self) -> "Database":
        if self._client is None:
            raise RuntimeError("Database is not initialized yet")
        return self._client

    def __getattr__(self, name: str):
        return getattr(self.get(), name)


db = _DatabaseProxy()


def set_db(database: "Database") -> None:
    db.set(database)


def get_db() -> "Database":
    return db.get()


class _DbConnectionContext:
    """Async context manager for direct SQLAlchemy-backed queries."""

    def __init__(self, session_factory: async_sessionmaker):
        self._session_factory = session_factory
        self._session: Optional[AsyncSession] = None

    async def __aenter__(self) -> _DbQueryConnection:
        self._session = self._session_factory()
        return _DbQueryConnection(self._session)

    async def __aexit__(self, exc_type, exc, tb):
        if self._session is not None:
            if exc_type is None:
                await self._session.commit()
            else:
                await self._session.rollback()
            await self._session.close()


class Database:
    """Async PostgreSQL database manager with caching."""

    def __init__(
        self, engine: AsyncEngine, session_factory: async_sessionmaker
    ):
        self.engine = engine
        self.session_factory = session_factory
        # XXX: Cache for expensive queries (family trees, friend circles)
        # XXX: 60 second TTL ensures data stays reasonably fresh
        self._cache = TTLCache(ttl_seconds=60)

    @classmethod
    async def create(cls, database_url: str) -> "Database":
        """Create a new database instance with connection pool."""
        url = database_url
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+asyncpg://", 1)
        elif url.startswith("postgresql://") and "+asyncpg" not in url:
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        engine = create_async_engine(url, pool_size=10, max_overflow=0)
        factory = async_sessionmaker(
            engine, expire_on_commit=False, class_=AsyncSession
        )
        db = cls(engine, factory)
        await db._init_tables()
        return db

    async def close(self):
        """Close the database connection pool."""
        await self.engine.dispose()

    def connection(self) -> _DbConnectionContext:
        """Get a lightweight query connection context."""
        return _DbConnectionContext(self.session_factory)

    async def fetch(self, query: str, *args):
        """Fetch multiple rows from a raw SQL query using SQLAlchemy."""
        async with self.connection() as conn:
            return await conn.fetch(query, *args)

    async def fetchrow(self, query: str, *args):
        """Fetch a single row from a raw SQL query using SQLAlchemy."""
        async with self.connection() as conn:
            return await conn.fetchrow(query, *args)

    async def fetchval(self, query: str, *args):
        """Fetch a single scalar value from a raw SQL query."""
        async with self.connection() as conn:
            return await conn.fetchval(query, *args)

    async def execute(self, query: str, *args):
        """Execute a raw SQL statement using SQLAlchemy."""
        async with self.connection() as conn:
            return await conn.execute(query, *args)

    async def _init_tables(self):
        """Initialize all database tables using ORM metadata."""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            # Runtime migration: keep unlocked_crafts.combos available for
            # existing databases that predate combo tracking.
            await conn.execute(
                text(
                    "ALTER TABLE unlocked_crafts "
                    "ADD COLUMN IF NOT EXISTS combos JSONB DEFAULT '[]'::jsonb"
                )
            )
            await conn.execute(
                text(
                    "UPDATE unlocked_crafts SET combos = '[]'::jsonb "
                    "WHERE combos IS NULL"
                )
            )
            await conn.execute(
                text(
                    "ALTER TABLE unlocked_crafts "
                    "ALTER COLUMN combos SET DEFAULT '[]'::jsonb"
                )
            )
            await conn.execute(
                text(
                    "ALTER TABLE unlocked_crafts "
                    "ALTER COLUMN combos SET NOT NULL"
                )
            )
            # Runtime migration: keep gardens.total_harvests available for
            # existing databases so garden achievements track correctly.
            await conn.execute(
                text(
                    "ALTER TABLE gardens "
                    "ADD COLUMN IF NOT EXISTS total_harvests BIGINT DEFAULT 0"
                )
            )
            await conn.execute(
                text(
                    "UPDATE gardens SET total_harvests = 0 "
                    "WHERE total_harvests IS NULL"
                )
            )
            await conn.execute(
                text(
                    "ALTER TABLE gardens "
                    "ALTER COLUMN total_harvests SET DEFAULT 0"
                )
            )
            await conn.execute(
                text(
                    "ALTER TABLE gardens "
                    "ALTER COLUMN total_harvests SET NOT NULL"
                )
            )
            # Best-effort backfill for legacy users using current harvest inventory.
            await conn.execute(
                text("""
                UPDATE gardens g
                SET total_harvests = inv.total_qty
                FROM (
                    SELECT user_id, COALESCE(SUM(quantity), 0) AS total_qty
                    FROM inventory
                    WHERE item_type = 'harvest'
                    GROUP BY user_id
                ) inv
                WHERE g.owner_id = inv.user_id
                  AND g.total_harvests = 0
                """)
            )
            # Runtime migration: relax the ordering check on relationship tables
            # from "user1_id < user2_id" to "user1_id <> user2_id". Application
            # code still canonicalises pairs on insert; the loose constraint just
            # lets admin/manual SQL inserts work in either order.
            for tbl in ("marriages", "siblings", "friendships"):
                await conn.execute(
                    text(
                        f"ALTER TABLE {tbl} "
                        f"DROP CONSTRAINT IF EXISTS {tbl}_check"
                    )
                )
                await conn.execute(
                    text(
                        f"ALTER TABLE {tbl} "
                        f"DROP CONSTRAINT IF EXISTS {tbl}_users_distinct"
                    )
                )
                await conn.execute(
                    text(
                        f"ALTER TABLE {tbl} "
                        f"ADD CONSTRAINT {tbl}_users_distinct "
                        f"CHECK (user1_id <> user2_id)"
                    )
                )

            # Runtime migration: add primary_adopter_id column for adoption limits
            # keep track of who initiated each adoption (for divorce/adoption cap logic)
            await conn.execute(
                text(
                    "ALTER TABLE family_relationships "
                    "ADD COLUMN IF NOT EXISTS primary_adopter_id BIGINT "
                    "REFERENCES users(user_id)"
                )
            )
            # Runtime migration: optional gender field for users
            await conn.execute(
                text(
                    "ALTER TABLE users "
                    "ADD COLUMN IF NOT EXISTS gender VARCHAR(20)"
                )
            )
            # Runtime migration: track who a /transferaccount source moved onto.
            # Funeral system relies on the source row staying queryable.
            await conn.execute(
                text(
                    "ALTER TABLE users "
                    "ADD COLUMN IF NOT EXISTS transferred BIGINT"
                )
            )
            # Backfill primary_adopter_id for existing relationships
            await conn.execute(
                text("""
                UPDATE family_relationships
                SET primary_adopter_id = parent_id
                WHERE primary_adopter_id IS NULL
                """)
            )
            # Runtime migration: reset chats table to requested schema.
            col_rows = await conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'chats'"
                )
            )
            existing_cols = {row[0] for row in col_rows.fetchall()}
            expected_cols = {
                "chat_id",
                "title",
                "type",
                "username",
                "photo",
                "description",
                "dc_id",
                "members_count",
                "is_forum",
                "is_members_hidden",
                "is_restricted",
                "restriction_reason",
                "created_at",
                "updated_at",
            }
            if existing_cols and existing_cols != expected_cols:
                await conn.execute(text("DROP TABLE IF EXISTS chats"))
                await conn.execute(
                    text(
                        "CREATE TABLE chats ("
                        "chat_id BIGINT PRIMARY KEY, "
                        "title VARCHAR NULL, "
                        "type VARCHAR NULL, "
                        "username VARCHAR NULL, "
                        "photo VARCHAR NULL, "
                        "description TEXT NULL, "
                        "dc_id INTEGER NULL, "
                        "members_count INTEGER NULL, "
                        "is_forum BOOLEAN NULL, "
                        "is_members_hidden BOOLEAN NULL, "
                        "is_restricted BOOLEAN NULL, "
                        "restriction_reason TEXT NULL, "
                        "created_at TIMESTAMP DEFAULT NOW(), "
                        "updated_at TIMESTAMP DEFAULT NOW()"
                        ")"
                    )
                )
            # Runtime migration: allow multiple pets per user (one per type).
            # Drop old single-column unique on user_id, add composite unique.
            await conn.execute(
                text(
                    "ALTER TABLE pets DROP CONSTRAINT IF EXISTS pets_user_id_key"
                )
            )
            await conn.execute(
                text(
                    "DO $$ BEGIN "
                    "ALTER TABLE pets ADD CONSTRAINT uq_pets_user_pet_type "
                    "UNIQUE (user_id, pet_type); "
                    "EXCEPTION WHEN duplicate_object OR duplicate_table THEN NULL; "
                    "END $$;"
                )
            )
            # Runtime migration: auto-harvest columns on gardens
            await conn.execute(
                text(
                    "ALTER TABLE gardens "
                    "ADD COLUMN IF NOT EXISTS auto_harvest BOOLEAN NOT NULL DEFAULT FALSE"
                )
            )
            await conn.execute(
                text(
                    "ALTER TABLE gardens "
                    "ADD COLUMN IF NOT EXISTS notify_chat_id BIGINT"
                )
            )
        # Seed marriage quotes if empty
        async with self.session_factory() as session:
            count = await session.scalar(select(func.count(MarriageQuote.id)))
            if not count:
                await self._seed_marriage_quotes(session)

    async def _seed_marriage_quotes(self, session: AsyncSession):
        """Seed initial marriage quotes."""
        quotes = [
            ("Two souls, one heart.", False),
            ("Love conquers all.", False),
            ("Together forever.", False),
            ("A perfect match.", False),
            ("Written in the stars.", False),
            ("Love finds a way.", False),
            ("Happily ever after begins now.", False),
            ("Two hearts become one.", False),
            ("Love knows no bounds... or limits!", True),
            ("The more the merrier!", True),
            ("Why have one when you can have more?", True),
            ("Building a bigger family, one spouse at a time.", True),
            ("Love multiplied, not divided.", True),
        ]
        session.add_all([
            MarriageQuote(quote=q, is_remarriage=r) for q, r in quotes
        ])
        await session.commit()

    # ========== User Methods ==========

    async def get_user(self, user_id: int):
        """Get a user by ID."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(User).where(User.user_id == user_id)
            )
            return _result_one(result)

    async def set_gender(self, user_id: int, gender: Optional[str]) -> None:
        """Set or clear a user's gender (free-text, app-side validated)."""
        async with self.session_factory() as session:
            await session.execute(
                update(User)
                .where(User.user_id == user_id)
                .values(gender=gender)
            )
            await session.commit()

    async def get_user_by_username(self, username: str):
        """Get a user by username."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(User).where(User.username == username)
            )
            return _result_one(result)

    async def upsert_user(
        self,
        user_id: int,
        username: Optional[str] = None,
        first_name: Optional[str] = None,
        profile_pic_file_id: Optional[str] = None,
    ):
        """Insert or update a user."""
        if not first_name:
            user = await self.get_user(user_id)
            if user is None:
                return None
            user.id = user.user_id
            return user

        async with self.session_factory() as session:
            stmt = pg_insert(User).values(
                user_id=user_id,
                username=username,
                first_name=first_name,
                profile_pic_file_id=profile_pic_file_id,
            )
            excluded = stmt.excluded
            stmt = stmt.on_conflict_do_update(
                index_elements=["user_id"],
                set_={
                    "username": func.coalesce(excluded.username, User.username),
                    "first_name": func.coalesce(
                        excluded.first_name, User.first_name
                    ),
                    "profile_pic_file_id": func.coalesce(
                        excluded.profile_pic_file_id, User.profile_pic_file_id
                    ),
                    "last_updated": func.now(),
                },
            ).returning(User)
            result = await session.execute(stmt)
            await session.commit()
            user = _result_one(result)
            user.id = user.user_id
            return user

    async def set_profile_pic(
        self,
        user_id: int,
        file_id: Optional[str] = None,
        b64: Optional[str] = None,
    ):
        """Set user's profile picture (file ID and/or base64)."""
        async with self.session_factory() as session:
            values = {}
            if file_id is not None:
                values["profile_pic_file_id"] = file_id
            if b64 is not None:
                values["profile_pic_b64"] = b64
            if values:
                await session.execute(
                    update(User).where(User.user_id == user_id).values(**values)
                )
                await session.commit()

    async def upsert_chat(
        self,
        chat: Any,
    ):
        """Insert or update selected chat metadata; ignore private chats."""
        if chat is None or getattr(chat, "id", None) is None:
            return

        chat_type_obj = getattr(chat, "type", None)
        chat_type = str(chat_type_obj) if chat_type_obj is not None else None
        if chat_type and chat_type.lower().endswith("private"):
            return

        photo = getattr(chat, "photo", None)
        photo_id = getattr(photo, "small_file_id", None) if photo else None

        restrictions = getattr(chat, "restrictions", None) or []
        restriction_reason = None
        if restrictions:
            reasons = []
            for item in restrictions:
                if isinstance(item, str):
                    reasons.append(item)
                    continue
                reason = getattr(item, "reason", None)
                platform = getattr(item, "platform", None)
                if reason and platform:
                    reasons.append(f"{platform}:{reason}")
                elif reason:
                    reasons.append(str(reason))
                elif platform:
                    reasons.append(str(platform))
            restriction_reason = "; ".join(reasons) if reasons else None

        async with self.session_factory() as session:
            stmt = pg_insert(Chat).values(
                chat_id=chat.id,
                chat_type=chat_type,
                title=getattr(chat, "title", None),
                username=getattr(chat, "username", None),
                photo=photo_id,
                description=getattr(chat, "description", None),
                dc_id=getattr(chat, "dc_id", None),
                members_count=getattr(chat, "members_count", None),
                is_forum=getattr(chat, "is_forum", None),
                is_members_hidden=getattr(chat, "is_members_hidden", None),
                is_restricted=getattr(chat, "is_restricted", None),
                restriction_reason=restriction_reason,
            )
            excluded = stmt.excluded
            stmt = stmt.on_conflict_do_update(
                index_elements=["chat_id"],
                set_={
                    "type": func.coalesce(excluded.type, Chat.chat_type),
                    "title": func.coalesce(excluded.title, Chat.title),
                    "username": func.coalesce(excluded.username, Chat.username),
                    "photo": func.coalesce(excluded.photo, Chat.photo),
                    "description": func.coalesce(
                        excluded.description, Chat.description
                    ),
                    "dc_id": func.coalesce(excluded.dc_id, Chat.dc_id),
                    "members_count": func.coalesce(
                        excluded.members_count, Chat.members_count
                    ),
                    "is_forum": func.coalesce(excluded.is_forum, Chat.is_forum),
                    "is_members_hidden": func.coalesce(
                        excluded.is_members_hidden, Chat.is_members_hidden
                    ),
                    "is_restricted": func.coalesce(
                        excluded.is_restricted, Chat.is_restricted
                    ),
                    "restriction_reason": func.coalesce(
                        excluded.restriction_reason, Chat.restriction_reason
                    ),
                    "updated_at": func.now(),
                },
            )
            await session.execute(stmt)
            await session.commit()

    # ========== Family Relationship Methods ==========

    async def get_parents(self, user_id: int):
        """Get all parents of a user."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(User)
                .join(
                    FamilyRelationship,
                    FamilyRelationship.parent_id == User.user_id,
                )
                .where(FamilyRelationship.child_id == user_id)
            )
            return _result_all(result)

    async def get_children(self, user_id: int):
        """Get all children of a user."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(User)
                .join(
                    FamilyRelationship,
                    FamilyRelationship.child_id == User.user_id,
                )
                .where(FamilyRelationship.parent_id == user_id)
            )
            return _result_all(result)

    async def get_siblings(self, user_id: int):
        """Get all siblings of a user (shared parents OR direct sibling relationship)."""
        async with self.session_factory() as session:
            # Via shared parents
            via_parents = (
                select(FamilyRelationship.child_id.label("sibling_id"))
                .where(
                    FamilyRelationship.parent_id.in_(
                        select(FamilyRelationship.parent_id).where(
                            FamilyRelationship.child_id == user_id
                        )
                    )
                )
                .where(FamilyRelationship.child_id != user_id)
            )
            # Direct siblings (user is user1)
            direct1 = select(Sibling.user2_id.label("sibling_id")).where(
                Sibling.user1_id == user_id
            )
            # Direct siblings (user is user2)
            direct2 = select(Sibling.user1_id.label("sibling_id")).where(
                Sibling.user2_id == user_id
            )
            all_sibling_ids = union(via_parents, direct1, direct2).subquery()
            result = await session.execute(
                select(User).where(
                    User.user_id.in_(select(all_sibling_ids.c.sibling_id))
                )
            )
            return _result_all(result)

    async def get_spouses(self, user_id: int):
        """Get all spouses of a user."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(User)
                .join(
                    Marriage,
                    or_(
                        Marriage.user1_id == User.user_id,
                        Marriage.user2_id == User.user_id,
                    ),
                )
                .where(
                    or_(
                        Marriage.user1_id == user_id,
                        Marriage.user2_id == user_id,
                    ),
                    User.user_id != user_id,
                )
            )
            return _result_all(result)

    async def is_ancestor(self, ancestor_id: int, descendant_id: int) -> bool:
        """Check if ancestor_id is an ancestor of descendant_id."""
        async with self.session_factory() as session:
            result = await session.scalar(
                text("""
                WITH RECURSIVE ancestors AS (
                    SELECT parent_id, child_id FROM family_relationships WHERE child_id = :desc_id
                    UNION
                    SELECT fr.parent_id, fr.child_id
                    FROM family_relationships fr
                    JOIN ancestors a ON fr.child_id = a.parent_id
                )
                SELECT EXISTS(SELECT 1 FROM ancestors WHERE parent_id = :anc_id)
                """),
                {"anc_id": ancestor_id, "desc_id": descendant_id},
            )
            return result

    async def is_descendant(self, descendant_id: int, ancestor_id: int) -> bool:
        """Check if descendant_id is a descendant of ancestor_id (child, grandchild, etc.)."""
        return await self.is_ancestor(ancestor_id, descendant_id)

    async def are_siblings(self, user1_id: int, user2_id: int) -> bool:
        """Check if two users are siblings (shared parent, direct, or transitive via BFS)."""
        if user1_id == user2_id:
            return False
        async with self.session_factory() as session:
            direct = await session.scalar(
                text("""
                SELECT EXISTS(
                    -- Via shared parent
                    SELECT 1 FROM family_relationships fr1
                    JOIN family_relationships fr2 ON fr1.parent_id = fr2.parent_id
                    WHERE fr1.child_id = :u1 AND fr2.child_id = :u2
                    UNION
                    -- Direct sibling relationship
                    SELECT 1 FROM siblings WHERE user1_id = :u1min AND user2_id = :u2max
                )
                """),
                {
                    "u1": user1_id,
                    "u2": user2_id,
                    "u1min": min(user1_id, user2_id),
                    "u2max": max(user1_id, user2_id),
                },
            )
            if direct:
                return True

            # Check transitive sibling relationship via BFS
            rows_result = await session.execute(
                select(Sibling.user1_id, Sibling.user2_id)
            )
            rows = rows_result.fetchall()
            adj = {}
            for r in rows:
                adj.setdefault(r[0], set()).add(r[1])
                adj.setdefault(r[1], set()).add(r[0])

            visited = {user1_id}
            queue = [user1_id]
            while queue:
                current = queue.pop(0)
                if current == user2_id:
                    return True
                for neighbor in adj.get(current, []):
                    if neighbor not in visited:
                        visited.add(neighbor)
                        queue.append(neighbor)

            return False

    async def get_sibling_path(
        self, user1_id: int, user2_id: int
    ) -> Optional[list]:
        """
        Find the transitive sibling path between two users via BFS.
        Returns the path as a list of user IDs from user1 to user2, or None if no path.
        """
        if user1_id == user2_id:
            return None

        async with self.session_factory() as session:
            rows_result = await session.execute(
                select(Sibling.user1_id, Sibling.user2_id)
            )
            rows = rows_result.fetchall()
            adj = {}
            for r in rows:
                adj.setdefault(r[0], set()).add(r[1])
                adj.setdefault(r[1], set()).add(r[0])

            visited: dict[int, Optional[int]] = {user1_id: None}
            queue = [user1_id]

            while queue:
                current = queue.pop(0)
                if current == user2_id:
                    path = []
                    node = user2_id
                    while node is not None:
                        path.append(node)
                        node = visited[node]
                    path.reverse()
                    return path

                for neighbor in adj.get(current, []):
                    if neighbor not in visited:
                        visited[neighbor] = current
                        queue.append(neighbor)

            return None

    async def get_family_path(
        self, user1_id: int, user2_id: int
    ) -> Optional[list]:
        """
        Find a path between two users through the combined family graph.
        Traverses siblings, parent-child, and marriage edges via BFS.
        """
        if user1_id == user2_id:
            return [(user1_id, "start")]

        async with self.session_factory() as session:
            adj: dict = {}

            # Sibling edges
            result = await session.execute(
                select(Sibling.user1_id, Sibling.user2_id)
            )
            for r in result.fetchall():
                adj.setdefault(r[0], []).append((r[1], "👫"))
                adj.setdefault(r[1], []).append((r[0], "👫"))

            # Parent-child edges
            result = await session.execute(
                select(
                    FamilyRelationship.parent_id, FamilyRelationship.child_id
                )
            )
            for r in result.fetchall():
                adj.setdefault(r[0], []).append((r[1], "👶"))
                adj.setdefault(r[1], []).append((r[0], "👤"))

            # Marriage edges
            result = await session.execute(
                select(Marriage.user1_id, Marriage.user2_id)
            )
            for r in result.fetchall():
                adj.setdefault(r[0], []).append((r[1], "💑"))
                adj.setdefault(r[1], []).append((r[0], "💑"))

            visited: dict = {user1_id: (None, None)}
            queue = [user1_id]

            while queue:
                current = queue.pop(0)
                if current == user2_id:
                    path = []
                    node = user2_id
                    while node is not None:
                        parent, label = visited[node]
                        path.append((node, label or ""))
                        node = parent
                    path.reverse()
                    path[0] = (path[0][0], "start")
                    return path

                for neighbor, label in adj.get(current, []):
                    if neighbor not in visited:
                        visited[neighbor] = (current, label)
                        queue.append(neighbor)

            return None

    async def are_close_family(self, user1_id: int, user2_id: int) -> bool:
        """Check if two users are connected via any family relationship path."""
        if user1_id == user2_id:
            return True
        return await self.get_family_path(user1_id, user2_id) is not None

    async def get_generation_level(self, user_id: int) -> int:
        """Get the generation level of a user.
        Level 0: Users with no parents (root)
        Level 1: Children of level 0
        Level N: Children of level N-1
        """
        async with self.session_factory() as session:
            visited = set()
            queue = [(user_id, 0)]
            max_depth = 0

            while queue:
                current_id, depth = queue.pop(0)
                if current_id in visited:
                    continue
                visited.add(current_id)
                max_depth = max(max_depth, depth)

                # Get parents of current user
                result = await session.execute(
                    select(FamilyRelationship.parent_id).where(
                        FamilyRelationship.child_id == current_id
                    )
                )
                parents = result.fetchall()
                if not parents:
                    # This is a root node (no parents)
                    return max_depth

                for parent in parents:
                    queue.append((parent[0], depth + 1))

            return max_depth

    async def is_ancestor_of(
        self, ancestor_id: int, descendant_id: int
    ) -> bool:
        """Check if ancestor_id is an ancestor (parent, grandparent, etc.) of descendant_id."""
        if ancestor_id == descendant_id:
            return False
        async with self.session_factory() as session:
            visited = set()
            queue = [descendant_id]
            while queue:
                current = queue.pop(0)
                if current == ancestor_id:
                    return True
                if current in visited:
                    continue
                visited.add(current)
                result = await session.execute(
                    select(FamilyRelationship.parent_id).where(
                        FamilyRelationship.child_id == current
                    )
                )
                for p in result.fetchall():
                    queue.append(p[0])
            return False

    async def is_descendant_of(
        self, descendant_id: int, ancestor_id: int
    ) -> bool:
        """Check if descendant_id is a descendant of ancestor_id."""
        return await self.is_ancestor_of(ancestor_id, descendant_id)

    async def is_spouse_of(self, user1_id: int, user2_id: int) -> bool:
        """Check if two users are married."""
        return await self.are_married(user1_id, user2_id)

    async def is_sibling_hierarchy_conflict(
        self, user1_id: int, user2_id: int
    ) -> bool:
        """
        Check if making user1 and user2 siblings would create a hierarchy conflict.
        Allows same-generation peers regardless of whether they share a parent:
        cousins, step-siblings, unrelated users at level 0, etc.
        Blocks: same user, ancestor/descendant, spouse, in-law, different
        generation when in a connected tree.
        """
        if user1_id == user2_id:
            return True
        if await self.is_ancestor_of(user1_id, user2_id):
            return True
        if await self.is_ancestor_of(user2_id, user1_id):
            return True
        if await self.is_spouse_of(user1_id, user2_id):
            return True

        # Same-generation check only within a connected tree. Unrelated
        # users (both at independent level 0) can always become siblings.
        if await self.are_close_family(user1_id, user2_id):
            user1_level = await self.get_generation_level(user1_id)
            user2_level = await self.get_generation_level(user2_id)
            if user1_level != user2_level:
                return True

        # In-law block: one's spouse cannot be the other's sibling, and
        # one's sibling cannot be the other's spouse.
        for primary, other in ((user1_id, user2_id), (user2_id, user1_id)):
            primary_spouses = await self.get_spouses(primary)
            for spouse in primary_spouses:
                spouse_siblings = await self.get_siblings(spouse["user_id"])
                if any(s["user_id"] == other for s in spouse_siblings):
                    return True
            primary_siblings = await self.get_siblings(primary)
            for sibling in primary_siblings:
                sibling_spouses = await self.get_spouses(sibling["user_id"])
                if any(s["user_id"] == other for s in sibling_spouses):
                    return True

        return False

    async def is_in_law_of(self, user1_id: int, user2_id: int) -> bool:
        """Check if user2 is an in-law of user1."""
        user1_spouses = await self.get_spouses(user1_id)
        for spouse in user1_spouses:
            spouse_siblings = await self.get_siblings(spouse["user_id"])
            if any(s["user_id"] == user2_id for s in spouse_siblings):
                return True
        user1_siblings = await self.get_siblings(user1_id)
        for sibling in user1_siblings:
            sibling_spouses = await self.get_spouses(sibling["user_id"])
            if any(s["user_id"] == user2_id for s in sibling_spouses):
                return True
        return False

    async def is_inlaw_of(self, user_id: int, other_id: int) -> bool:
        """
        Return True if other_id is an in-law of user_id — meaning they are
        reachable ONLY through a marriage edge at some point in the path,
        and not a direct blood/adoption relative.

        Concretely: other_id is an in-law if:
          - they are a spouse of user_id's blood relative (parent, child, sibling,
            ancestor, descendant), OR
          - they are a blood relative of user_id's spouse.
        """
        if user_id == other_id:
            return False

        # Gather user_id's blood/adoption relatives (no marriage traversal)
        blood_relatives = await self._get_blood_relatives(user_id)
        # If other_id is a spouse of any blood relative → in-law
        for rel_id in blood_relatives:
            if await self.are_married(rel_id, other_id):
                return True

        # Gather user_id's spouses
        spouses = await self.get_spouses(user_id)
        for spouse in spouses:
            spouse_blood = await self._get_blood_relatives(spouse["user_id"])
            if other_id in spouse_blood:
                return True

        return False

    async def _get_blood_relatives(self, user_id: int) -> set[int]:
        """
        BFS over parent-child and sibling edges only (no marriage).
        Returns all users reachable via blood/adoption links from user_id.
        """
        async with self.session_factory() as session:
            # Build adjacency from parent-child and sibling edges
            adj: dict[int, list[int]] = {}

            result = await session.execute(
                select(
                    FamilyRelationship.parent_id, FamilyRelationship.child_id
                )
            )
            for parent, child in result.fetchall():
                adj.setdefault(parent, []).append(child)
                adj.setdefault(child, []).append(parent)

            result = await session.execute(
                select(Sibling.user1_id, Sibling.user2_id)
            )
            for u1, u2 in result.fetchall():
                adj.setdefault(u1, []).append(u2)
                adj.setdefault(u2, []).append(u1)

            visited: set[int] = set()
            queue = [user_id]
            while queue:
                current = queue.pop(0)
                if current in visited:
                    continue
                visited.add(current)
                for neighbor in adj.get(current, []):
                    if neighbor not in visited:
                        queue.append(neighbor)
            visited.discard(user_id)  # don't include self
            return visited

    async def is_adopt_hierarchy_conflict(
        self, parent_id: int, child_id: int
    ) -> bool:
        """
        Return True if parent_id adopting child_id would be invalid.

        Allowed cases:
        - child has no parents and is not connected to parent's tree
        - child has no parents but IS connected (only if generation levels are correct)
        - child already has a parent who is parent_id's spouse (co-adoption)

        Blocked cases:
        - self-adoption
        - child is ancestor of parent (circular)
        - child is already parent's parent
        - they are spouses
        - they are siblings
        - child already has parents and adopter is not married to any of them
        """
        if parent_id == child_id:
            return True

        # Can't adopt your own ancestor
        if await self.is_ancestor_of(child_id, parent_id):
            return True

        # Can't adopt someone who is already your parent
        parents = await self.get_parents(parent_id)
        if any(p["user_id"] == child_id for p in parents):
            return True

        # Can't adopt your spouse
        if await self.is_spouse_of(parent_id, child_id):
            return True

        # Can't adopt your sibling
        if await self.are_siblings(parent_id, child_id):
            return True

        target_parents = await self.get_parents(child_id)
        if not target_parents:
            return False

        # Target has parents: co-adoption allowed only if adopter is married to one.
        existing_parent_ids = {p["user_id"] for p in target_parents}
        if parent_id in existing_parent_ids:
            return True
        adopter_spouses = await self.get_spouses(parent_id)
        adopter_spouse_ids = {s["user_id"] for s in adopter_spouses}
        if not (adopter_spouse_ids & existing_parent_ids):
            return True

        return False

    async def add_sibling(self, user1_id: int, user2_id: int):
        """Add a direct sibling relationship."""
        u1, u2 = min(user1_id, user2_id), max(user1_id, user2_id)
        async with self.session_factory() as session:
            await session.execute(
                pg_insert(Sibling)
                .values(user1_id=u1, user2_id=u2)
                .on_conflict_do_nothing()
            )
            await session.commit()
        # XXX: Invalidate family cache for both users
        self._cache.invalidate(f"close_family:{user1_id}")
        self._cache.invalidate(f"close_family:{user2_id}")

    async def remove_sibling(self, user1_id: int, user2_id: int):
        """Remove a direct sibling relationship."""
        u1, u2 = min(user1_id, user2_id), max(user1_id, user2_id)
        async with self.session_factory() as session:
            await session.execute(
                delete(Sibling).where(
                    Sibling.user1_id == u1, Sibling.user2_id == u2
                )
            )
            await session.commit()
        # XXX: Invalidate family cache for both users
        self._cache.invalidate(f"close_family:{user1_id}")
        self._cache.invalidate(f"close_family:{user2_id}")

    async def is_direct_sibling(self, user1_id: int, user2_id: int) -> bool:
        """Check if two users have a direct sibling relationship (not via parents)."""
        u1, u2 = min(user1_id, user2_id), max(user1_id, user2_id)
        async with self.session_factory() as session:
            return bool(
                await session.scalar(
                    select(
                        exists().where(
                            and_(Sibling.user1_id == u1, Sibling.user2_id == u2)
                        )
                    )
                )
            )

    async def are_married(self, user1_id: int, user2_id: int) -> bool:
        """Check if two users are married."""
        u1, u2 = min(user1_id, user2_id), max(user1_id, user2_id)
        async with self.session_factory() as session:
            return bool(
                await session.scalar(
                    select(
                        exists().where(
                            and_(
                                Marriage.user1_id == u1, Marriage.user2_id == u2
                            )
                        )
                    )
                )
            )

    async def add_adoption(self, parent_id: int, child_id: int):
        """Add a parent-child relationship."""
        async with self.session_factory() as session:
            await session.execute(
                insert(FamilyRelationship).values(
                    parent_id=parent_id,
                    child_id=child_id,
                    primary_adopter_id=parent_id,
                )
            )
            await session.commit()
        # XXX: Invalidate family cache for both users
        self._cache.invalidate(f"close_family:{parent_id}")
        self._cache.invalidate(f"close_family:{child_id}")

    async def remove_adoption(self, parent_id: int, child_id: int):
        """Remove a parent-child relationship only if this user initiated it."""
        async with self.session_factory() as session:
            await session.execute(
                delete(FamilyRelationship).where(
                    FamilyRelationship.parent_id == parent_id,
                    FamilyRelationship.child_id == child_id,
                    FamilyRelationship.primary_adopter_id == parent_id,
                )
            )
            await session.commit()
        # XXX: Invalidate family cache for both users
        self._cache.invalidate(f"close_family:{parent_id}")
        self._cache.invalidate(f"close_family:{child_id}")

    async def add_marriage(self, user1_id: int, user2_id: int):
        """Add a marriage between two users."""
        u1, u2 = min(user1_id, user2_id), max(user1_id, user2_id)
        async with self.session_factory() as session:
            await session.execute(
                insert(Marriage).values(user1_id=u1, user2_id=u2)
            )
            await session.commit()
        # XXX: Invalidate family cache for both users
        self._cache.invalidate(f"close_family:{user1_id}")
        self._cache.invalidate(f"close_family:{user2_id}")

    async def remove_marriage(self, user1_id: int, user2_id: int):
        """Remove a marriage between two users."""
        u1, u2 = min(user1_id, user2_id), max(user1_id, user2_id)
        async with self.session_factory() as session:
            await session.execute(
                delete(Marriage).where(
                    Marriage.user1_id == u1, Marriage.user2_id == u2
                )
            )
            await session.commit()
        # XXX: Invalidate family cache for both users
        self._cache.invalidate(f"close_family:{user1_id}")
        self._cache.invalidate(f"close_family:{user2_id}")

    async def get_marriage_count(self, user_id: int) -> int:
        """Get the number of current marriages for a user."""
        async with self.session_factory() as session:
            return (
                await session.scalar(
                    select(func.count(Marriage.id)).where(
                        or_(
                            Marriage.user1_id == user_id,
                            Marriage.user2_id == user_id,
                        )
                    )
                )
                or 0
            )

    async def get_random_marriage_quote(
        self, is_remarriage: bool = False
    ) -> str:
        """Get a random marriage quote."""
        async with self.session_factory() as session:
            result = await session.scalar(
                select(MarriageQuote.quote)
                .where(MarriageQuote.is_remarriage == is_remarriage)
                .order_by(func.random())
                .limit(1)
            )
            return result or "Congratulations!"

    async def get_adopted_children_count(self, user_id: int) -> int:
        """Count how many children this user has adopted (where they are primary_adopter)."""
        async with self.session_factory() as session:
            return (
                await session.scalar(
                    select(func.count(FamilyRelationship.id)).where(
                        FamilyRelationship.primary_adopter_id == user_id
                    )
                )
                or 0
            )

    async def is_child_already_adopted_by_spouse(
        self, user_id: int, target_id: int
    ) -> bool:
        """Check if the user's spouse has already adopted the target."""
        async with self.session_factory() as session:
            spouses = await self.get_spouses(user_id)
            if not spouses:
                return False

            spouse_ids = [s["user_id"] for s in spouses]

            # Check if any spouse has adopted the target
            result = await session.scalar(
                select(func.count(FamilyRelationship.id)).where(
                    FamilyRelationship.primary_adopter_id.in_(spouse_ids),
                    FamilyRelationship.child_id == target_id,
                )
            )
            return (result or 0) > 0

    # ========== Pending Request Methods ==========

    async def create_pending_request(
        self,
        request_type: str,
        requester_id: int,
        target_id: int,
        chat_id: int,
        message_id: Optional[int] = None,
    ):
        """Create a pending request."""
        async with self.session_factory() as session:
            async with session.begin():
                await session.execute(
                    delete(PendingRequest).where(
                        PendingRequest.request_type == request_type,
                        PendingRequest.requester_id == requester_id,
                        PendingRequest.target_id == target_id,
                    )
                )
                result = await session.execute(
                    insert(PendingRequest)
                    .values(
                        request_type=request_type,
                        requester_id=requester_id,
                        target_id=target_id,
                        chat_id=chat_id,
                        message_id=message_id,
                    )
                    .returning(PendingRequest)
                )
                return _result_one(result)

    async def get_pending_request(
        self, request_type: str, requester_id: int, target_id: int
    ):
        """Get a specific pending request."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(PendingRequest).where(
                    PendingRequest.request_type == request_type,
                    PendingRequest.requester_id == requester_id,
                    PendingRequest.target_id == target_id,
                    PendingRequest.expires_at > func.now(),
                )
            )
            return _result_one(result)

    async def get_pending_request_by_id(self, request_id: int):
        """Get a pending request by ID."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(PendingRequest).where(
                    PendingRequest.id == request_id,
                    PendingRequest.expires_at > func.now(),
                )
            )
            return _result_one(result)

    async def delete_pending_request(self, request_id: int):
        """Delete a pending request."""
        async with self.session_factory() as session:
            await session.execute(
                delete(PendingRequest).where(PendingRequest.id == request_id)
            )
            await session.commit()

    async def cleanup_expired_requests(self):
        """Delete all expired pending requests."""
        async with self.session_factory() as session:
            await session.execute(
                delete(PendingRequest).where(
                    PendingRequest.expires_at <= func.now()
                )
            )
            await session.commit()

    # ========== Friendship Methods ==========

    async def get_friends(self, user_id: int):
        """Get all friends of a user."""
        # XXX: Check cache first
        cache_key = f"friends:{user_id}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        async with self.session_factory() as session:
            result = await session.execute(
                select(User)
                .join(
                    Friendship,
                    or_(
                        Friendship.user1_id == User.user_id,
                        Friendship.user2_id == User.user_id,
                    ),
                )
                .where(
                    or_(
                        Friendship.user1_id == user_id,
                        Friendship.user2_id == user_id,
                    ),
                    User.user_id != user_id,
                )
            )
            friends = _result_all(result)

        # XXX: Cache the result
        self._cache.set(cache_key, friends)
        return friends

    async def are_friends(self, user1_id: int, user2_id: int) -> bool:
        """Check if two users are friends."""
        u1, u2 = min(user1_id, user2_id), max(user1_id, user2_id)
        async with self.session_factory() as session:
            return bool(
                await session.scalar(
                    select(
                        exists().where(
                            and_(
                                Friendship.user1_id == u1,
                                Friendship.user2_id == u2,
                            )
                        )
                    )
                )
            )

    async def add_friendship(self, user1_id: int, user2_id: int):
        """Add a friendship between two users."""
        u1, u2 = min(user1_id, user2_id), max(user1_id, user2_id)
        async with self.session_factory() as session:
            await session.execute(
                pg_insert(Friendship)
                .values(user1_id=u1, user2_id=u2)
                .on_conflict_do_nothing()
            )
            await session.commit()
        # XXX: Invalidate friends cache for both users
        self._cache.invalidate(f"friends:{user1_id}")
        self._cache.invalidate(f"friends:{user2_id}")

    async def remove_friendship(self, user1_id: int, user2_id: int):
        """Remove a friendship between two users."""
        u1, u2 = min(user1_id, user2_id), max(user1_id, user2_id)
        async with self.session_factory() as session:
            await session.execute(
                delete(Friendship).where(
                    Friendship.user1_id == u1, Friendship.user2_id == u2
                )
            )
            await session.commit()
        # XXX: Invalidate friends cache for both users
        self._cache.invalidate(f"friends:{user1_id}")
        self._cache.invalidate(f"friends:{user2_id}")

    async def create_friend_request(self, requester_id: int, target_id: int):
        """Create a friend request."""
        async with self.session_factory() as session:
            await session.execute(
                pg_insert(FriendRequest)
                .values(requester_id=requester_id, target_id=target_id)
                .on_conflict_do_nothing()
            )
            await session.commit()

    async def get_friend_request(self, requester_id: int, target_id: int):
        """Get a friend request."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(FriendRequest).where(
                    FriendRequest.requester_id == requester_id,
                    FriendRequest.target_id == target_id,
                )
            )
            return _result_one(result)

    async def delete_friend_request(self, requester_id: int, target_id: int):
        """Delete a friend request."""
        async with self.session_factory() as session:
            await session.execute(
                delete(FriendRequest).where(
                    FriendRequest.requester_id == requester_id,
                    FriendRequest.target_id == target_id,
                )
            )
            await session.commit()

    async def get_friend_suggestions(self, user_id: int, limit: int = 10):
        """Get friend suggestions (friends of friends who aren't already friends)."""
        async with self.session_factory() as session:
            result = await session.execute(
                text("""
                SELECT DISTINCT u.*, COUNT(*) as mutual_count
                FROM users u
                JOIN friendships f1 ON (f1.user1_id = u.user_id OR f1.user2_id = u.user_id)
                JOIN friendships f2 ON (
                    (f2.user1_id = :uid OR f2.user2_id = :uid)
                    AND (
                        (f1.user1_id = f2.user1_id AND f1.user1_id != :uid)
                        OR (f1.user1_id = f2.user2_id AND f1.user1_id != :uid)
                        OR (f1.user2_id = f2.user1_id AND f1.user2_id != :uid)
                        OR (f1.user2_id = f2.user2_id AND f1.user2_id != :uid)
                    )
                )
                WHERE u.user_id != :uid
                AND NOT EXISTS (
                    SELECT 1 FROM friendships f
                    WHERE (f.user1_id = :uid AND f.user2_id = u.user_id)
                    OR (f.user1_id = u.user_id AND f.user2_id = :uid)
                )
                GROUP BY u.user_id
                ORDER BY mutual_count DESC
                LIMIT :lim
                """),
                {"uid": user_id, "lim": limit},
            )
            return _result_all(result)

    # ========== Friend Rating Methods ==========

    async def set_friend_rating(
        self, rater_id: int, rated_id: int, rating: int
    ):
        """Set or update a friend rating."""
        async with self.session_factory() as session:
            stmt = pg_insert(FriendRating).values(
                rater_id=rater_id, rated_id=rated_id, rating=rating
            )
            excluded = stmt.excluded
            stmt = stmt.on_conflict_do_update(
                index_elements=["rater_id", "rated_id"],
                set_={"rating": excluded.rating},
            )
            await session.execute(stmt)
            await session.commit()

    async def get_ratings_given(self, user_id: int):
        """Get all ratings given by a user."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(
                    FriendRating.rater_id,
                    FriendRating.rated_id,
                    FriendRating.rating,
                    FriendRating.created_at,
                    User.first_name,
                    User.username,
                )
                .join(User, User.user_id == FriendRating.rated_id)
                .where(FriendRating.rater_id == user_id)
                .order_by(FriendRating.rating.desc())
            )
            return _result_all(result)

    async def get_average_rating(self, user_id: int) -> Optional[float]:
        """Get average rating received by a user."""
        async with self.session_factory() as session:
            result = await session.scalar(
                select(func.avg(FriendRating.rating)).where(
                    FriendRating.rated_id == user_id
                )
            )
            return float(result) if result is not None else None

    # ========== Friend Link Methods ==========

    async def get_or_create_friend_link(self, user_id: int) -> str:
        """Get or create a friend link code for a user."""
        async with self.session_factory() as session:
            existing = await session.scalar(
                select(FriendLink.link_code).where(
                    FriendLink.user_id == user_id
                )
            )
            if existing:
                return existing
            link_code = secrets.token_urlsafe(16)
            await session.execute(
                insert(FriendLink).values(user_id=user_id, link_code=link_code)
            )
            await session.commit()
            return link_code

    async def get_user_by_friend_link(self, link_code: str):
        """Get user by friend link code."""
        async with self.session_factory() as session:
            user_id = await session.scalar(
                select(FriendLink.user_id).where(
                    FriendLink.link_code == link_code
                )
            )
        if user_id:
            return await self.get_user(user_id)
        return None

    # ========== Wallet Methods ==========

    async def _ensure_user_stub(self, session, user_id: int) -> None:
        """Insert a minimal users row if one doesn't exist, satisfying FK constraints."""
        await session.execute(
            text(
                "INSERT INTO users (user_id) VALUES (:uid) ON CONFLICT (user_id) DO NOTHING"
            ),
            {"uid": user_id},
        )

    async def get_wallet(self, user_id: int):
        """Get or create a wallet for a user."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(Wallet).where(Wallet.user_id == user_id)
            )
            row = _result_one(result)
            if row:
                return row
            await self._ensure_user_stub(session, user_id)
            stmt = (
                pg_insert(Wallet)
                .values(user_id=user_id)
                .on_conflict_do_nothing()
                .returning(Wallet)
            )
            result = await session.execute(stmt)
            await session.commit()
            return _result_one(result)

    async def add_balance(self, user_id: int, amount: int, reason: str):
        """Add to user's balance and record transaction."""
        async with self.session_factory() as session:
            async with session.begin():
                await self._ensure_user_stub(session, user_id)
                await session.execute(
                    text("""
                    INSERT INTO wallets (user_id, balance, total_earned)
                    VALUES (:uid, CAST(:amt AS bigint), GREATEST(0, CAST(:amt AS bigint)))
                    ON CONFLICT (user_id) DO UPDATE SET
                        balance = wallets.balance + CAST(:amt AS bigint),
                        total_earned = CASE WHEN CAST(:amt AS bigint) > 0 THEN wallets.total_earned + CAST(:amt AS bigint) ELSE wallets.total_earned END,
                        last_updated = CURRENT_TIMESTAMP
                    """),
                    {"uid": user_id, "amt": amount},
                )
                await session.execute(
                    insert(Transaction).values(
                        user_id=user_id, amount=amount, reason=reason
                    )
                )

    async def get_transactions(self, user_id: int, limit: int = 10):
        """Get recent transactions for a user."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(Transaction)
                .where(Transaction.user_id == user_id)
                .order_by(Transaction.created_at.desc())
                .limit(limit)
            )
            return _result_all(result)

    # ========== Bank Account Methods ==========

    async def get_bank_account(self, user_id: int):
        """Get or create a bank account for a user."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(BankAccount).where(BankAccount.user_id == user_id)
            )
            row = _result_one(result)
            if row:
                return row
            stmt = (
                pg_insert(BankAccount)
                .values(user_id=user_id)
                .on_conflict_do_nothing()
                .returning(BankAccount)
            )
            result = await session.execute(stmt)
            await session.commit()
            return _result_one(result)

    async def add_bank_balance(self, user_id: int, amount: int):
        """Add funds directly to a user's bank account."""
        if amount <= 0:
            return
        async with self.session_factory() as session:
            stmt = pg_insert(BankAccount).values(
                user_id=user_id, balance=amount
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["user_id"],
                set_={
                    "balance": BankAccount.balance + amount,
                    "last_updated": func.now(),
                },
            )
            await session.execute(stmt)
            await session.commit()

    async def subtract_bank_balance(
        self, user_id: int, amount: int, reason: str
    ) -> int:
        """Subtract from a user's bank balance (clamped to zero); logs as
        a negative transaction. Returns the amount actually subtracted.
        """
        if amount <= 0:
            return 0
        async with self.session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    text(
                        "SELECT balance FROM bank_accounts "
                        "WHERE user_id = :uid FOR UPDATE"
                    ),
                    {"uid": user_id},
                )
                account = _result_one(result)
                if not account or account["balance"] <= 0:
                    return 0
                actual = min(amount, account["balance"])
                await session.execute(
                    text(
                        "UPDATE bank_accounts SET balance = balance - :amt, "
                        "last_updated = CURRENT_TIMESTAMP "
                        "WHERE user_id = :uid"
                    ),
                    {"uid": user_id, "amt": actual},
                )
                await session.execute(
                    insert(Transaction).values(
                        user_id=user_id, amount=-actual, reason=reason
                    )
                )
                return actual

    async def deposit_to_bank(self, user_id: int, amount: int) -> bool:
        """Deposit money from wallet to bank. Returns True on success."""
        if amount <= 0:
            return False
        async with self.session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    text(
                        "SELECT balance FROM wallets WHERE user_id = :uid FOR UPDATE"
                    ),
                    {"uid": user_id},
                )
                wallet = _result_one(result)
                if not wallet or wallet["balance"] < amount:
                    return False
                await session.execute(
                    text(
                        "UPDATE wallets SET balance = balance - CAST(:amt AS bigint) WHERE user_id = :uid"
                    ),
                    {"uid": user_id, "amt": amount},
                )
                await session.execute(
                    text("""
                    INSERT INTO bank_accounts (user_id, balance)
                    VALUES (:uid, CAST(:amt AS bigint))
                    ON CONFLICT (user_id) DO UPDATE SET
                        balance = bank_accounts.balance + CAST(:amt AS bigint),
                        last_updated = CURRENT_TIMESTAMP
                    """),
                    {"uid": user_id, "amt": amount},
                )
                await session.execute(
                    insert(Transaction).values(
                        user_id=user_id, amount=-amount, reason="Bank deposit"
                    )
                )
                return True

    async def withdraw_from_bank(self, user_id: int, amount: int) -> bool:
        """Withdraw money from bank to wallet. Returns True on success."""
        if amount <= 0:
            return False
        async with self.session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    text(
                        "SELECT balance FROM bank_accounts WHERE user_id = :uid FOR UPDATE"
                    ),
                    {"uid": user_id},
                )
                account = _result_one(result)
                if not account or account["balance"] < amount:
                    return False
                await session.execute(
                    text(
                        "UPDATE bank_accounts SET balance = balance - :amt, last_updated = CURRENT_TIMESTAMP WHERE user_id = :uid"
                    ),
                    {"uid": user_id, "amt": amount},
                )
                await session.execute(
                    text("""
                    INSERT INTO wallets (user_id, balance)
                    VALUES (:uid, :amt)
                    ON CONFLICT (user_id) DO UPDATE SET
                        balance = wallets.balance + :amt,
                        last_updated = CURRENT_TIMESTAMP
                    """),
                    {"uid": user_id, "amt": amount},
                )
                await session.execute(
                    insert(Transaction).values(
                        user_id=user_id, amount=amount, reason="Bank withdrawal"
                    )
                )
                return True

    # ========== Feedback Chat Methods ==========

    async def add_feedback_chat(
        self, chat_id: int, chat_name: str, added_by: int
    ):
        """Add a chat as a feedback destination."""
        async with self.session_factory() as session:
            stmt = pg_insert(FeedbackChat).values(
                chat_id=chat_id, chat_name=chat_name, added_by=added_by
            )
            excluded = stmt.excluded
            stmt = stmt.on_conflict_do_update(
                index_elements=["chat_id"],
                set_={"chat_name": excluded.chat_name},
            )
            await session.execute(stmt)
            await session.commit()

    async def remove_feedback_chat(self, chat_id: int):
        """Remove a chat from feedback destinations."""
        async with self.session_factory() as session:
            await session.execute(
                delete(FeedbackChat).where(FeedbackChat.chat_id == chat_id)
            )
            await session.commit()

    async def get_feedback_chats(self):
        """Get all feedback destination chats."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(FeedbackChat).order_by(FeedbackChat.created_at)
            )
            return _result_all(result)

    # ========== Family Tree Query Methods ==========

    async def get_ancestors(self, user_id: int, max_depth: int = 3):
        """Get ancestors up to max_depth generations."""
        async with self.session_factory() as session:
            result = await session.execute(
                text("""
                WITH RECURSIVE ancestors AS (
                    SELECT parent_id, child_id, 1 as depth FROM family_relationships WHERE child_id = :uid
                    UNION ALL
                    SELECT fr.parent_id, fr.child_id, a.depth + 1
                    FROM family_relationships fr
                    JOIN ancestors a ON fr.child_id = a.parent_id
                    WHERE a.depth < :max_depth
                )
                SELECT u.*, a.depth FROM users u
                JOIN ancestors a ON u.user_id = a.parent_id
                ORDER BY a.depth
                """),
                {"uid": user_id, "max_depth": max_depth},
            )
            rows = _result_all(result)
            return [(row, row["depth"]) for row in rows]

    async def get_descendants(self, user_id: int, max_depth: int = 3):
        """Get descendants up to max_depth generations."""
        async with self.session_factory() as session:
            result = await session.execute(
                text("""
                WITH RECURSIVE descendants AS (
                    SELECT parent_id, child_id, 1 as depth FROM family_relationships WHERE parent_id = :uid
                    UNION ALL
                    SELECT fr.parent_id, fr.child_id, d.depth + 1
                    FROM family_relationships fr
                    JOIN descendants d ON fr.parent_id = d.child_id
                    WHERE d.depth < :max_depth
                )
                SELECT u.*, d.depth FROM users u
                JOIN descendants d ON u.user_id = d.child_id
                ORDER BY d.depth
                """),
                {"uid": user_id, "max_depth": max_depth},
            )
            rows = _result_all(result)
            return [(row, row["depth"]) for row in rows]

    async def get_close_family(self, user_id: int) -> dict:
        """Get close family members (parents, children, siblings, spouses)."""
        # XXX: Check cache first
        cache_key = f"close_family:{user_id}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        parents = await self.get_parents(user_id)
        children = await self.get_children(user_id)
        siblings = await self.get_siblings(user_id)
        spouses = await self.get_spouses(user_id)
        result = {
            "parents": parents,
            "children": children,
            "siblings": siblings,
            "spouses": spouses,
        }

        # XXX: Cache the result
        self._cache.set(cache_key, result)
        return result

    async def get_extended_family(self, user_id: int) -> dict:
        """Get extended family including siblings' families and in-laws."""
        result = {
            "spouses": await self.get_spouses(user_id),
            "parents": await self.get_parents(user_id),
            "siblings": await self.get_siblings(user_id),
            "children": await self.get_children(user_id),
            "extended_siblings": [],
            "nieces_nephews": [],
            "in_laws": [],
            "grandchildren": [],
        }

        seen_ids = {user_id}
        direct_sibling_ids = {s["user_id"] for s in result["siblings"]}

        for sib in result["siblings"]:
            sib_id = sib["user_id"]
            seen_ids.add(sib_id)
            sib_siblings = await self.get_siblings(sib_id)
            for ss in sib_siblings:
                ss_id = ss["user_id"]
                if (
                    ss_id not in seen_ids
                    and ss_id != user_id
                    and ss_id not in direct_sibling_ids
                ):
                    seen_ids.add(ss_id)
                    result["extended_siblings"].append(ss)

        all_siblings = result["siblings"] + result["extended_siblings"]
        for sib in all_siblings:
            sib_children = await self.get_children(sib["user_id"])
            for sc in sib_children:
                if sc["user_id"] not in seen_ids:
                    seen_ids.add(sc["user_id"])
                    result["nieces_nephews"].append(sc)

        for sib in all_siblings:
            sib_spouses = await self.get_spouses(sib["user_id"])
            for ss in sib_spouses:
                if ss["user_id"] not in seen_ids:
                    seen_ids.add(ss["user_id"])
                    result["in_laws"].append(ss)

        for spouse in result["spouses"]:
            spouse_parents = await self.get_parents(spouse["user_id"])
            for sp in spouse_parents:
                if sp["user_id"] not in seen_ids:
                    seen_ids.add(sp["user_id"])
                    result["in_laws"].append(sp)

        for child in result["children"]:
            child_children = await self.get_children(child["user_id"])
            for gc in child_children:
                if gc["user_id"] not in seen_ids:
                    seen_ids.add(gc["user_id"])
                    result["grandchildren"].append(gc)

        return result

    async def get_full_family_tree(self, user_id: int) -> dict:
        """
        Get the entire family tree for a user in a single optimized query.
        Uses recursive CTEs to fetch all related users, their relationships,
        and marriages in just a few queries.
        """
        async with self.session_factory() as session:
            result = await session.execute(
                text("""
                WITH RECURSIVE family AS (
                    SELECT CAST(:uid AS BIGINT) as user_id, 0 as depth

                    UNION

                    SELECT fr.parent_id, f.depth - 1
                    FROM family_relationships fr
                    JOIN family f ON fr.child_id = f.user_id
                    WHERE f.depth > -10

                    UNION

                    SELECT fr.child_id, f.depth + 1
                    FROM family_relationships fr
                    JOIN family f ON fr.parent_id = f.user_id
                    WHERE f.depth < 10
                ),
                with_spouses AS (
                    SELECT user_id FROM family
                    UNION
                    SELECT CASE WHEN m.user1_id = f.user_id THEN m.user2_id ELSE m.user1_id END
                    FROM marriages m
                    JOIN family f ON m.user1_id = f.user_id OR m.user2_id = f.user_id
                ),
                with_siblings AS (
                    SELECT user_id FROM with_spouses
                    UNION
                    SELECT CASE WHEN s.user1_id = ws.user_id THEN s.user2_id ELSE s.user1_id END
                    FROM siblings s
                    JOIN with_spouses ws ON s.user1_id = ws.user_id OR s.user2_id = ws.user_id
                )
                SELECT DISTINCT u.*
                FROM users u
                JOIN with_siblings ws ON u.user_id = ws.user_id
                """),
                {"uid": user_id},
            )
            related_ids = _result_all(result)

            if not related_ids:
                return {
                    "members": {},
                    "parent_child": [],
                    "marriages": [],
                    "siblings": [],
                }

            members = {row["user_id"]: row for row in related_ids}
            member_ids = list(members.keys())

            rel_result = await session.execute(
                text("""
                SELECT parent_id, child_id FROM family_relationships
                WHERE parent_id = ANY(:ids) AND child_id = ANY(:ids)
                """),
                {"ids": member_ids},
            )
            parent_child = [
                (r["parent_id"], r["child_id"]) for r in _result_all(rel_result)
            ]

            mar_result = await session.execute(
                text("""
                SELECT user1_id, user2_id FROM marriages
                WHERE user1_id = ANY(:ids) AND user2_id = ANY(:ids)
                """),
                {"ids": member_ids},
            )
            marriages = [
                (r["user1_id"], r["user2_id"]) for r in _result_all(mar_result)
            ]

            sib_result = await session.execute(
                text("""
                SELECT user1_id, user2_id FROM siblings
                WHERE user1_id = ANY(:ids) AND user2_id = ANY(:ids)
                """),
                {"ids": member_ids},
            )
            siblings = [
                (r["user1_id"], r["user2_id"]) for r in _result_all(sib_result)
            ]

            return {
                "members": members,
                "parent_child": parent_child,
                "marriages": marriages,
                "siblings": siblings,
            }

    # ========== Admin Methods ==========

    async def reset_user(self, user_id: int):
        """Reset all data for a user (admin only)."""
        async with self.session_factory() as session:
            async with session.begin():
                await session.execute(
                    delete(FamilyRelationship).where(
                        or_(
                            FamilyRelationship.parent_id == user_id,
                            FamilyRelationship.child_id == user_id,
                        )
                    )
                )
                await session.execute(
                    delete(Marriage).where(
                        or_(
                            Marriage.user1_id == user_id,
                            Marriage.user2_id == user_id,
                        )
                    )
                )
                await session.execute(
                    delete(Sibling).where(
                        or_(
                            Sibling.user1_id == user_id,
                            Sibling.user2_id == user_id,
                        )
                    )
                )
                await session.execute(
                    delete(Friendship).where(
                        or_(
                            Friendship.user1_id == user_id,
                            Friendship.user2_id == user_id,
                        )
                    )
                )
                await session.execute(
                    delete(FriendRequest).where(
                        or_(
                            FriendRequest.requester_id == user_id,
                            FriendRequest.target_id == user_id,
                        )
                    )
                )
                await session.execute(
                    delete(FriendRating).where(
                        or_(
                            FriendRating.rater_id == user_id,
                            FriendRating.rated_id == user_id,
                        )
                    )
                )
                await session.execute(
                    delete(FriendLink).where(FriendLink.user_id == user_id)
                )
                await session.execute(
                    delete(PendingRequest).where(
                        or_(
                            PendingRequest.requester_id == user_id,
                            PendingRequest.target_id == user_id,
                        )
                    )
                )
                await session.execute(
                    delete(Transaction).where(Transaction.user_id == user_id)
                )
                await session.execute(
                    delete(Wallet).where(Wallet.user_id == user_id)
                )
                await session.execute(
                    delete(DailyReward).where(DailyReward.user_id == user_id)
                )
                await session.execute(
                    delete(GemFuseRequest).where(
                        or_(
                            GemFuseRequest.requester_id == user_id,
                            GemFuseRequest.target_id == user_id,
                        )
                    )
                )
                await session.execute(
                    update(User)
                    .where(User.user_id == user_id)
                    .values(profile_pic_file_id=None)
                )

    # ========== Daily Reward Methods ==========

    async def get_daily_reward_status(self, user_id: int):
        """Get user's daily reward status."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(DailyReward).where(DailyReward.user_id == user_id)
            )
            return _result_one(result)

    async def can_claim_daily(self, user_id: int) -> bool:
        """Check if user can claim daily reward (resets at UTC midnight)."""
        async with self.session_factory() as session:
            last_date = await session.scalar(
                select(DailyReward.last_claim_date).where(
                    DailyReward.user_id == user_id
                )
            )
        if not last_date:
            return True
        from datetime import date

        return last_date < date.today()

    async def claim_daily_reward(self, user_id: int, gem_type: str) -> bool:
        """Claim daily reward. Returns True if successful."""
        async with self.session_factory() as session:
            stmt = pg_insert(DailyReward).values(
                user_id=user_id,
                last_claim_date=func.current_date(),
                current_gem=gem_type,
            )
            excluded = stmt.excluded
            stmt = stmt.on_conflict_do_update(
                index_elements=["user_id"],
                set_={
                    "last_claim_date": func.current_date(),
                    "current_gem": excluded.current_gem,
                },
            )
            await session.execute(stmt)
            await session.commit()
        return True

    async def get_user_gem(self, user_id: int) -> Optional[str]:
        """Get user's current gem type."""
        async with self.session_factory() as session:
            return await session.scalar(
                select(DailyReward.current_gem).where(
                    DailyReward.user_id == user_id
                )
            )

    async def clear_user_gem(self, user_id: int):
        """Clear user's gem after fusing."""
        async with self.session_factory() as session:
            await session.execute(
                update(DailyReward)
                .where(DailyReward.user_id == user_id)
                .values(current_gem=None)
            )
            await session.commit()

    # ========== Gem Fuse Methods ==========

    async def create_gem_fuse_request(
        self, requester_id: int, target_id: int, gem_type: str, chat_id: int
    ):
        """Create a gem fuse request."""
        async with self.session_factory() as session:
            stmt = pg_insert(GemFuseRequest).values(
                requester_id=requester_id,
                target_id=target_id,
                gem_type=gem_type,
                chat_id=chat_id,
            )
            excluded = stmt.excluded
            stmt = stmt.on_conflict_do_update(
                index_elements=["requester_id", "target_id"],
                set_={
                    "gem_type": excluded.gem_type,
                    "chat_id": excluded.chat_id,
                    "created_at": func.now(),
                    "expires_at": text("CURRENT_TIMESTAMP + INTERVAL '1 hour'"),
                },
            ).returning(GemFuseRequest)
            result = await session.execute(stmt)
            await session.commit()
            return _result_one(result)

    async def get_gem_fuse_request(self, request_id: int):
        """Get a gem fuse request by ID."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(GemFuseRequest).where(GemFuseRequest.id == request_id)
            )
            return _result_one(result)

    async def delete_gem_fuse_request(self, request_id: int):
        """Delete a gem fuse request."""
        async with self.session_factory() as session:
            await session.execute(
                delete(GemFuseRequest).where(GemFuseRequest.id == request_id)
            )
            await session.commit()

    # ========== Factory Methods ==========

    async def get_or_create_factory(self, user_id: int):
        """Get or create a user's factory."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(Factory).where(Factory.owner_id == user_id)
            )
            factory = _result_one(result)
            if factory:
                return factory
            stmt = insert(Factory).values(owner_id=user_id).returning(Factory)
            result = await session.execute(stmt)
            await session.commit()
            return _result_one(result)

    async def get_factory(self, user_id: int):
        """Get a user's factory."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(Factory).where(Factory.owner_id == user_id)
            )
            return _result_one(result)

    async def expand_factory(self, factory_id: int, new_capacity: int) -> bool:
        """Expand factory capacity."""
        async with self.session_factory() as session:
            await session.execute(
                update(Factory)
                .where(Factory.id == factory_id)
                .values(capacity=new_capacity)
            )
            await session.commit()
        return True

    async def get_or_create_worker(self, user_id: int, name: str):
        """Get or create a worker profile for a user."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(Worker).where(Worker.user_id == user_id)
            )
            worker = _result_one(result)
            if worker:
                return worker
            stmt = (
                insert(Worker)
                .values(user_id=user_id, name=name)
                .returning(Worker)
            )
            result = await session.execute(stmt)
            await session.commit()
            return _result_one(result)

    async def get_worker(self, user_id: int):
        """Get a worker by user_id."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(Worker).where(Worker.user_id == user_id)
            )
            return _result_one(result)

    async def get_worker_by_id(self, worker_id: int):
        """Get a worker by worker_id."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(Worker).where(Worker.id == worker_id)
            )
            return _result_one(result)

    async def update_worker_xp(self, worker_id: int, xp_gain: int):
        """Add XP to a worker."""
        async with self.session_factory() as session:
            await session.execute(
                update(Worker)
                .where(Worker.id == worker_id)
                .values(xp=Worker.xp + xp_gain)
            )
            await session.commit()

    async def get_factory_workers(self, factory_id: int):
        """Get all workers assigned to a factory."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(
                    Worker.id,
                    Worker.user_id,
                    Worker.name,
                    Worker.xp,
                    Worker.created_at,
                    WorkerAssignment.fatigue,
                    WorkerAssignment.is_working,
                    WorkerAssignment.work_started_at,
                    WorkerAssignment.total_shifts,
                    WorkerAssignment.id.label("assignment_id"),
                )
                .join(WorkerAssignment, Worker.id == WorkerAssignment.worker_id)
                .where(WorkerAssignment.factory_id == factory_id)
                .order_by(WorkerAssignment.is_working.desc(), Worker.name)
            )
            return _result_all(result)

    async def get_worker_assignment(self, worker_id: int, factory_id: int):
        """Get a specific worker assignment."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(WorkerAssignment).where(
                    WorkerAssignment.worker_id == worker_id,
                    WorkerAssignment.factory_id == factory_id,
                )
            )
            return _result_one(result)

    async def hire_worker(self, factory_id: int, worker_id: int) -> bool:
        """Hire a worker to a factory."""
        async with self.session_factory() as session:
            try:
                await session.execute(
                    insert(WorkerAssignment).values(
                        worker_id=worker_id, factory_id=factory_id
                    )
                )
                await session.commit()
                return True
            except Exception:
                return False

    async def fire_worker(self, factory_id: int, worker_id: int) -> bool:
        """Remove a worker from a factory."""
        async with self.session_factory() as session:
            result = await session.execute(
                delete(WorkerAssignment).where(
                    WorkerAssignment.worker_id == worker_id,
                    WorkerAssignment.factory_id == factory_id,
                )
            )
            await session.commit()
            return result.rowcount > 0

    async def start_worker_shift(self, assignment_id: int):
        """Start a worker's shift."""
        async with self.session_factory() as session:
            await session.execute(
                update(WorkerAssignment)
                .where(WorkerAssignment.id == assignment_id)
                .values(is_working=True, work_started_at=func.now())
            )
            await session.commit()

    async def end_worker_shift(
        self,
        assignment_id: int,
        fatigue_gain: int,
        factory_id: int,
        earnings: int,
    ):
        """End a worker's shift and update stats."""
        async with self.session_factory() as session:
            async with session.begin():
                await session.execute(
                    update(WorkerAssignment)
                    .where(WorkerAssignment.id == assignment_id)
                    .values(
                        is_working=False,
                        work_started_at=None,
                        last_work_ended_at=func.now(),
                        fatigue=func.least(
                            WorkerAssignment.fatigue + fatigue_gain, 100
                        ),
                        total_shifts=WorkerAssignment.total_shifts + 1,
                    )
                )
                await session.execute(
                    update(Factory)
                    .where(Factory.id == factory_id)
                    .values(total_earnings=Factory.total_earnings + earnings)
                )

    async def reduce_worker_fatigue(
        self, assignment_id: int, fatigue_reduction: int
    ) -> int:
        """Reduce worker fatigue. Returns new fatigue level."""
        async with self.session_factory() as session:
            result = await session.execute(
                update(WorkerAssignment)
                .where(WorkerAssignment.id == assignment_id)
                .values(
                    fatigue=func.greatest(
                        WorkerAssignment.fatigue - fatigue_reduction, 0
                    )
                )
                .returning(WorkerAssignment.fatigue)
            )
            await session.commit()
            row = result.fetchone()
            return row[0] if row else 0

    async def is_worker_blocked(self, factory_id: int, worker_id: int) -> bool:
        """Check if a worker is blocked from a factory."""
        async with self.session_factory() as session:
            return bool(
                await session.scalar(
                    select(
                        exists().where(
                            and_(
                                FactoryHireBlock.factory_id == factory_id,
                                FactoryHireBlock.blocked_worker_id == worker_id,
                            )
                        )
                    )
                )
            )

    async def block_worker(self, factory_id: int, worker_id: int):
        """Block a worker from being hired at a factory."""
        async with self.session_factory() as session:
            await session.execute(
                pg_insert(FactoryHireBlock)
                .values(factory_id=factory_id, blocked_worker_id=worker_id)
                .on_conflict_do_nothing()
            )
            await session.commit()

    async def add_transfer_block(
        self, owner_id: int, blocked_user_id: int
    ) -> bool:
        """Mark blocked_user_id as not allowed to send money to owner_id."""
        async with self.session_factory() as session:
            result = await session.execute(
                pg_insert(TransferBlock)
                .values(owner_id=owner_id, blocked_user_id=blocked_user_id)
                .on_conflict_do_nothing()
            )
            await session.commit()
            return result.rowcount > 0

    async def remove_transfer_block(
        self, owner_id: int, blocked_user_id: int
    ) -> bool:
        """Remove a transfer block; returns True if a row was removed."""
        async with self.session_factory() as session:
            result = await session.execute(
                delete(TransferBlock).where(
                    TransferBlock.owner_id == owner_id,
                    TransferBlock.blocked_user_id == blocked_user_id,
                )
            )
            await session.commit()
            return result.rowcount > 0

    async def is_transfer_blocked(self, owner_id: int, sender_id: int) -> bool:
        """Return True if owner_id has blocked sender_id from transferring."""
        async with self.session_factory() as session:
            return bool(
                await session.scalar(
                    select(
                        exists().where(
                            and_(
                                TransferBlock.owner_id == owner_id,
                                TransferBlock.blocked_user_id == sender_id,
                            )
                        )
                    )
                )
            )

    async def record_funeral_donation(
        self, donor_id: int, deceased_id: int, amount: int
    ) -> bool:
        """Record a funeral donation. Returns False if donor already donated
        to this deceased account (unique constraint blocks the insert)."""
        async with self.session_factory() as session:
            try:
                await session.execute(
                    insert(FuneralDonation).values(
                        donor_user_id=donor_id,
                        deceased_user_id=deceased_id,
                        amount=amount,
                    )
                )
                await session.commit()
                return True
            except IntegrityError:
                await session.rollback()
                return False

    async def has_donated_to_funeral(
        self, donor_id: int, deceased_id: int
    ) -> bool:
        async with self.session_factory() as session:
            return bool(
                await session.scalar(
                    select(
                        exists().where(
                            and_(
                                FuneralDonation.donor_user_id == donor_id,
                                FuneralDonation.deceased_user_id == deceased_id,
                            )
                        )
                    )
                )
            )

    async def get_funeral_leaderboard(self, limit: int = 10):
        """Top donors by total funeral donation amount; ties broken by count."""
        async with self.session_factory() as session:
            result = await session.execute(
                text("""
                SELECT donor_user_id,
                       SUM(amount) AS total_amount,
                       COUNT(*)    AS funeral_count
                FROM funerals_history
                GROUP BY donor_user_id
                ORDER BY total_amount DESC, funeral_count DESC
                LIMIT :lim
                """),
                {"lim": limit},
            )
            return [dict(row._mapping) for row in result.fetchall()]

    async def list_transfer_blocks(self, owner_id: int) -> list[int]:
        """Return user_ids that the owner has blocked from transferring."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(TransferBlock.blocked_user_id).where(
                    TransferBlock.owner_id == owner_id
                )
            )
            return [row[0] for row in result.fetchall()]

    async def unblock_worker(self, factory_id: int, worker_id: int):
        """Unblock a worker from a factory."""
        async with self.session_factory() as session:
            await session.execute(
                delete(FactoryHireBlock).where(
                    FactoryHireBlock.factory_id == factory_id,
                    FactoryHireBlock.blocked_worker_id == worker_id,
                )
            )
            await session.commit()

    async def get_worker_factory_count(self, worker_id: int) -> int:
        """Get how many factories a worker is currently assigned to."""
        async with self.session_factory() as session:
            return (
                await session.scalar(
                    select(func.count(WorkerAssignment.id)).where(
                        WorkerAssignment.worker_id == worker_id
                    )
                )
                or 0
            )

    # ========== Garden Methods ==========

    async def get_or_create_garden(self, user_id: int):
        """Get or create a user's garden."""
        # Ensure owner exists to satisfy FK on gardens.owner_id -> users.user_id.
        await self.upsert_user(user_id)
        async with self.session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    text("SELECT * FROM gardens WHERE owner_id = :uid"),
                    {"uid": user_id},
                )
                garden = _result_one(result)
                if not garden:
                    result = await session.execute(
                        text(
                            "INSERT INTO gardens (owner_id) VALUES (:uid) RETURNING *"
                        ),
                        {"uid": user_id},
                    )
                    garden = _result_one(result)
                    garden_size = garden["size"]
                    for i in range(garden_size * garden_size):
                        await session.execute(
                            text(
                                "INSERT INTO garden_plots (garden_id, position) VALUES (:gid, :pos)"
                            ),
                            {"gid": garden["id"], "pos": i},
                        )
                return garden

    async def get_garden(self, user_id: int):
        """Get a user's garden."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(Garden).where(Garden.owner_id == user_id)
            )
            return _result_one(result)

    async def increment_garden_harvests(self, user_id: int, amount: int):
        """Increment cumulative harvested crop count for a user's garden."""
        if amount <= 0:
            return
        async with self.session_factory() as session:
            await session.execute(
                text("""
                UPDATE gardens
                SET total_harvests = COALESCE(total_harvests, 0) + :amount
                WHERE owner_id = :uid
                """),
                {"amount": amount, "uid": user_id},
            )
            await session.commit()

    async def expand_garden(self, garden_id: int, new_size: int) -> bool:
        """Expand garden size and add new plots."""
        async with self.session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    text("SELECT * FROM gardens WHERE id = :gid"),
                    {"gid": garden_id},
                )
                garden = _result_one(result)
                if not garden:
                    return False

                old_size = garden["size"]
                old_count = old_size * old_size
                new_count = new_size * new_size

                for i in range(old_count, new_count):
                    await session.execute(
                        text("""
                        INSERT INTO garden_plots (garden_id, position)
                        VALUES (:gid, :pos)
                        ON CONFLICT DO NOTHING
                        """),
                        {"gid": garden_id, "pos": i},
                    )

                await session.execute(
                    update(Garden)
                    .where(Garden.id == garden_id)
                    .values(size=new_size)
                )
                return True

    async def get_garden_plots(self, garden_id: int):
        """Get all plots in a garden."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(GardenPlot)
                .where(GardenPlot.garden_id == garden_id)
                .order_by(GardenPlot.position)
            )
            return _result_all(result)

    async def plant_crop(
        self, garden_id: int, position: int, crop_type: str
    ) -> bool:
        """Plant a crop in a garden plot."""
        async with self.session_factory() as session:
            result = await session.execute(
                update(GardenPlot)
                .where(
                    GardenPlot.garden_id == garden_id,
                    GardenPlot.position == position,
                    GardenPlot.crop_type == None,  # noqa: E711
                )
                .values(
                    crop_type=crop_type, planted_at=func.now(), is_ready=False
                )
            )
            await session.commit()
            return result.rowcount > 0

    async def plant_crops_batch(
        self, garden_id: int, positions: List[int], crop_type: str
    ) -> int:
        """Plant crops in multiple plots at once. Returns number planted."""
        if not positions:
            return 0
        async with self.session_factory() as session:
            result = await session.execute(
                update(GardenPlot)
                .where(
                    GardenPlot.garden_id == garden_id,
                    GardenPlot.position.in_(positions),
                    GardenPlot.crop_type == None,  # noqa: E711
                )
                .values(
                    crop_type=crop_type, planted_at=func.now(), is_ready=False
                )
            )
            await session.commit()
            return result.rowcount

    async def harvest_plot(
        self, garden_id: int, position: int
    ) -> Optional[str]:
        """Harvest a ready crop from a plot. Returns crop type.

        Concurrent harvests for the same user are already serialised by the
        per-user asyncio lock in bot/plugins/garden.py, so this just reads
        the ready plot then clears it within one transaction.
        """
        async with self.session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    text("""
                    SELECT crop_type FROM garden_plots
                    WHERE garden_id = :gid AND position = :pos AND is_ready = TRUE
                    """),
                    {"gid": garden_id, "pos": position},
                )
                plot = _result_one(result)
                if not plot or not plot["crop_type"]:
                    return None

                crop_type = plot["crop_type"]
                await session.execute(
                    update(GardenPlot)
                    .where(
                        GardenPlot.garden_id == garden_id,
                        GardenPlot.position == position,
                    )
                    .values(crop_type=None, planted_at=None, is_ready=False)
                )
                return crop_type

    async def harvest_plots_batch(
        self, garden_id: int, positions: List[int]
    ) -> List[str]:
        """Harvest multiple ready plots at once. Returns list of crop types.

        Serialised by the per-user asyncio lock in bot/plugins/garden.py
        — concurrent harvests for the same user cannot run.
        """
        if not positions:
            return []
        async with self.session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    text("""
                    SELECT position, crop_type FROM garden_plots
                    WHERE garden_id = :gid AND position = ANY(:positions)
                    AND is_ready = TRUE AND crop_type IS NOT NULL
                    """),
                    {"gid": garden_id, "positions": positions},
                )
                rows = _result_all(result)
                if not rows:
                    return []

                crop_types = [row["crop_type"] for row in rows]
                harvested_positions = [row["position"] for row in rows]

                await session.execute(
                    update(GardenPlot)
                    .where(
                        GardenPlot.garden_id == garden_id,
                        GardenPlot.position.in_(harvested_positions),
                    )
                    .values(crop_type=None, planted_at=None, is_ready=False)
                )
                return crop_types

    async def mark_plots_ready(
        self, garden_id: int, ready_positions: List[int]
    ):
        """Mark multiple plots as ready for harvest."""
        if not ready_positions:
            return
        async with self.session_factory() as session:
            await session.execute(
                update(GardenPlot)
                .where(
                    GardenPlot.garden_id == garden_id,
                    GardenPlot.position.in_(ready_positions),
                )
                .values(is_ready=True)
            )
            await session.commit()

    # ========== Inventory Methods ==========

    async def get_inventory(self, user_id: int):
        """Get all items in a user's inventory."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(Inventory)
                .where(Inventory.user_id == user_id, Inventory.quantity > 0)
                .order_by(Inventory.item_type, Inventory.item_name)
            )
            return _result_all(result)

    async def get_inventory_item(
        self, user_id: int, item_type: str, item_name: str
    ) -> int:
        """Get quantity of a specific item."""
        async with self.session_factory() as session:
            return (
                await session.scalar(
                    select(Inventory.quantity).where(
                        Inventory.user_id == user_id,
                        Inventory.item_type == item_type,
                        Inventory.item_name == item_name,
                    )
                )
                or 0
            )

    async def add_inventory_item(
        self, user_id: int, item_type: str, item_name: str, quantity: int
    ):
        """Add items to inventory."""
        async with self.session_factory() as session:
            stmt = pg_insert(Inventory).values(
                user_id=user_id,
                item_type=item_type,
                item_name=item_name,
                quantity=quantity,
            )
            excluded = stmt.excluded
            stmt = stmt.on_conflict_do_update(
                index_elements=["user_id", "item_type", "item_name"],
                set_={"quantity": Inventory.quantity + excluded.quantity},
            )
            await session.execute(stmt)
            await session.commit()

    async def remove_inventory_item(
        self, user_id: int, item_type: str, item_name: str, quantity: int
    ) -> bool:
        """Remove items from inventory. Returns True if successful."""
        async with self.session_factory() as session:
            result = await session.execute(
                update(Inventory)
                .where(
                    Inventory.user_id == user_id,
                    Inventory.item_type == item_type,
                    Inventory.item_name == item_name,
                    Inventory.quantity >= quantity,
                )
                .values(quantity=Inventory.quantity - quantity)
                .returning(Inventory.quantity)
            )
            await session.commit()
            return result.fetchone() is not None

    # ========== Gift Inventory Methods ==========

    async def get_gift_inventory(self, user_id: int):
        """Get all items in a user's gift inventory."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(GiftInventory)
                .where(
                    GiftInventory.user_id == user_id, GiftInventory.quantity > 0
                )
                .order_by(GiftInventory.item_type, GiftInventory.item_name)
            )
            return _result_all(result)

    async def get_gift_inventory_item(
        self, user_id: int, item_type: str, item_name: str
    ) -> int:
        """Get quantity of a specific gift item."""
        async with self.session_factory() as session:
            return (
                await session.scalar(
                    select(GiftInventory.quantity).where(
                        GiftInventory.user_id == user_id,
                        GiftInventory.item_type == item_type,
                        GiftInventory.item_name == item_name,
                    )
                )
                or 0
            )

    async def add_gift_inventory_item(
        self, user_id: int, item_type: str, item_name: str, quantity: int
    ):
        """Add items to gift inventory."""
        async with self.session_factory() as session:
            stmt = pg_insert(GiftInventory).values(
                user_id=user_id,
                item_type=item_type,
                item_name=item_name,
                quantity=quantity,
            )
            excluded = stmt.excluded
            stmt = stmt.on_conflict_do_update(
                index_elements=["user_id", "item_type", "item_name"],
                set_={"quantity": GiftInventory.quantity + excluded.quantity},
            )
            await session.execute(stmt)
            await session.commit()

    async def remove_gift_inventory_item(
        self, user_id: int, item_type: str, item_name: str, quantity: int
    ) -> bool:
        """Remove items from gift inventory. Returns True if successful."""
        async with self.session_factory() as session:
            result = await session.execute(
                update(GiftInventory)
                .where(
                    GiftInventory.user_id == user_id,
                    GiftInventory.item_type == item_type,
                    GiftInventory.item_name == item_name,
                    GiftInventory.quantity >= quantity,
                )
                .values(quantity=GiftInventory.quantity - quantity)
                .returning(GiftInventory.quantity)
            )
            await session.commit()
            return result.fetchone() is not None

    # ========== Machine Methods ==========

    async def get_user_machines(self, user_id: int):
        """Get all machines a user owns."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(UserMachine).where(UserMachine.user_id == user_id)
            )
            return _result_all(result)

    async def has_machine(self, user_id: int, machine_type: str) -> bool:
        """Check if user owns a specific machine."""
        async with self.session_factory() as session:
            return bool(
                await session.scalar(
                    select(
                        exists().where(
                            and_(
                                UserMachine.user_id == user_id,
                                UserMachine.machine_type == machine_type,
                            )
                        )
                    )
                )
            )

    async def buy_machine(self, user_id: int, machine_type: str) -> bool:
        """Purchase a machine. Returns True if successful."""
        async with self.session_factory() as session:
            try:
                await session.execute(
                    insert(UserMachine).values(
                        user_id=user_id, machine_type=machine_type
                    )
                )
                await session.commit()
                return True
            except Exception:
                return False

    # ========== Animal Farm Methods ==========

    async def get_user_pens(self, user_id: int) -> list:
        """Get all animal pens owned by user."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(AnimalPen)
                .where(AnimalPen.user_id == user_id)
                .order_by(AnimalPen.pen_type)
            )
            return _result_all(result)

    async def get_pen_by_type(self, user_id: int, pen_type: str):
        """Get a specific pen by type, or None."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(AnimalPen).where(
                    AnimalPen.user_id == user_id,
                    AnimalPen.pen_type == pen_type,
                )
            )
            return _result_one(result)

    async def buy_animal_pen(self, user_id: int, pen_type: str) -> int:
        """Create a new pen for user. Returns pen id."""
        async with self.session_factory() as session:
            pen = AnimalPen(user_id=user_id, pen_type=pen_type, level=1)
            session.add(pen)
            await session.commit()
            await session.refresh(pen)
            return pen.id

    async def upgrade_animal_pen(self, user_id: int, pen_type: str) -> bool:
        """Increment pen level (max 3). Returns True if upgraded."""
        async with self.session_factory() as session:
            result = await session.execute(
                update(AnimalPen)
                .where(
                    AnimalPen.user_id == user_id,
                    AnimalPen.pen_type == pen_type,
                    AnimalPen.level < 3,
                )
                .values(level=AnimalPen.level + 1)
                .returning(AnimalPen.level)
            )
            await session.commit()
            return result.fetchone() is not None

    async def get_pen_animals(self, pen_id: int) -> list:
        """Get all animals in a pen ordered by id."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(Animal)
                .where(Animal.pen_id == pen_id)
                .order_by(Animal.id)
            )
            return _result_all(result)

    async def get_pen_animal_count(self, pen_id: int) -> int:
        """Count animals in a pen."""
        async with self.session_factory() as session:
            return (
                await session.scalar(
                    select(func.count()).where(Animal.pen_id == pen_id)
                )
                or 0
            )

    async def buy_animal(self, pen_id: int, animal_type: str) -> int:
        """Add an animal to a pen. Returns animal id."""
        async with self.session_factory() as session:
            animal = Animal(pen_id=pen_id, animal_type=animal_type)
            session.add(animal)
            await session.commit()
            await session.refresh(animal)
            return animal.id

    async def feed_animal(self, animal_id: int) -> bool:
        """Mark animal as fed and start the production timer. Returns True if found."""
        import datetime as dt
        from bot.constants import ANIMALS

        async with self.session_factory() as session:
            row = await session.get(Animal, animal_id)
            if not row:
                return False
            produce_time = ANIMALS[row.animal_type]["produce_time"]
            now = dt.datetime.utcnow()
            await session.execute(
                update(Animal)
                .where(Animal.id == animal_id)
                .values(
                    last_fed_at=now,
                    ready_at=now + dt.timedelta(seconds=produce_time),
                    is_ready=False,
                )
            )
            await session.commit()
            return True

    async def check_and_mark_ready(self, animal_id: int) -> bool:
        """If ready_at has passed, flip is_ready=True. Returns current is_ready value."""
        import datetime as dt

        async with self.session_factory() as session:
            row = await session.get(Animal, animal_id)
            if not row:
                return False
            if row.is_ready:
                return True
            if row.ready_at and row.ready_at <= dt.datetime.utcnow():
                await session.execute(
                    update(Animal)
                    .where(Animal.id == animal_id)
                    .values(is_ready=True)
                )
                await session.commit()
                return True
            return False

    async def collect_animal(self, animal_id: int) -> tuple[str, int] | None:
        """Collect produce from a ready animal, reset its state. Returns (produce_type, qty)."""
        import datetime as dt
        import random
        from bot.constants import ANIMALS

        async with self.session_factory() as session:
            row = await session.get(Animal, animal_id)
            if not row:
                return None
            # Re-check readiness in case check_and_mark_ready wasn't called first
            if (
                not row.is_ready
                and row.ready_at
                and row.ready_at <= dt.datetime.utcnow()
            ):
                row.is_ready = True
            if not row.is_ready:
                return None
            info = ANIMALS[row.animal_type]
            qty = random.randint(info["produce_min"], info["produce_max"])
            await session.execute(
                update(Animal)
                .where(Animal.id == animal_id)
                .values(is_ready=False, last_fed_at=None, ready_at=None)
            )
            await session.commit()
            return info["produce"], qty

    # ========== Pet Methods ==========

    @staticmethod
    def _pet_row_to_dict(row: "Pet") -> dict:
        return {
            "id": row.id,
            "user_id": row.user_id,
            "pet_type": row.pet_type,
            "pet_name": row.pet_name,
            "level": row.level,
            "happiness": row.happiness,
            "happiness_updated_at": row.happiness_updated_at,
            "created_at": row.created_at,
        }

    async def get_pet(self, user_id: int) -> dict | None:
        """Return the user's first/only pet (backward-compat for profile)."""
        async with self.session_factory() as session:
            row = await session.scalar(
                select(Pet)
                .where(Pet.user_id == user_id)
                .order_by(Pet.created_at)
            )
            return self._pet_row_to_dict(row) if row else None

    async def get_pets(self, user_id: int) -> list[dict]:
        """Return all pets owned by a user, ordered by acquisition."""
        async with self.session_factory() as session:
            rows = (
                await session.scalars(
                    select(Pet)
                    .where(Pet.user_id == user_id)
                    .order_by(Pet.created_at)
                )
            ).all()
            return [self._pet_row_to_dict(r) for r in rows]

    async def get_pet_by_type(self, user_id: int, pet_type: str) -> dict | None:
        async with self.session_factory() as session:
            row = await session.scalar(
                select(Pet).where(
                    Pet.user_id == user_id, Pet.pet_type == pet_type
                )
            )
            return self._pet_row_to_dict(row) if row else None

    async def buy_pet(self, user_id: int, pet_type: str) -> dict:
        from datetime import datetime

        async with self.session_factory() as session:
            now = datetime.utcnow()
            row = Pet(
                user_id=user_id,
                pet_type=pet_type,
                level=1,
                happiness=100,
                happiness_updated_at=now,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return self._pet_row_to_dict(row)

    async def change_pet(
        self, user_id: int, from_type: str, to_type: str
    ) -> bool:
        from datetime import datetime

        async with self.session_factory() as session:
            async with session.begin():
                row = await session.scalar(
                    select(Pet).where(
                        Pet.user_id == user_id, Pet.pet_type == from_type
                    )
                )
                if not row:
                    return False
                history = PetChangeHistory(
                    user_id=user_id,
                    old_pet_type=row.pet_type,
                    old_level=row.level,
                    new_pet_type=to_type,
                )
                session.add(history)
                now = datetime.utcnow()
                row.pet_type = to_type
                row.level = 1
                row.happiness = 100
                row.happiness_updated_at = now
            return True

    async def set_pet_name(
        self, user_id: int, pet_type: str, name: str | None
    ) -> bool:
        async with self.session_factory() as session:
            result = await session.execute(
                update(Pet)
                .where(Pet.user_id == user_id, Pet.pet_type == pet_type)
                .values(pet_name=name)
            )
            await session.commit()
            return result.rowcount > 0

    async def levelup_pet(self, user_id: int, pet_type: str) -> int:
        """Increment pet level, return new level."""
        async with self.session_factory() as session:
            row = await session.scalar(
                select(Pet).where(
                    Pet.user_id == user_id, Pet.pet_type == pet_type
                )
            )
            if not row:
                return 0
            row.level += 1
            await session.commit()
            return row.level

    async def update_pet_happiness(
        self, user_id: int, pet_type: str, gain: int
    ) -> int:
        """Apply time-based decay then add gain (capped 0-PET_MAX_HAPPINESS), return new value."""
        from datetime import datetime
        from bot.constants import PET_MAX_HAPPINESS

        async with self.session_factory() as session:
            row = await session.scalar(
                select(Pet).where(
                    Pet.user_id == user_id, Pet.pet_type == pet_type
                )
            )
            if not row:
                return 0
            now = datetime.utcnow()
            updated = row.happiness_updated_at
            elapsed_days = (now - updated).total_seconds() / 86400
            current = max(0, row.happiness - int(elapsed_days * 40))
            new_val = max(0, min(PET_MAX_HAPPINESS, current + gain))
            row.happiness = new_val
            row.happiness_updated_at = now
            await session.commit()
            return new_val

    # ========== Marketplace Methods ==========

    async def create_listing(
        self,
        seller_id: int,
        item_type: str,
        item_name: str,
        quantity: int,
        price_each: int,
    ):
        """Create a marketplace listing."""
        async with self.session_factory() as session:
            result = await session.execute(
                insert(MarketplaceListing)
                .values(
                    seller_id=seller_id,
                    item_type=item_type,
                    item_name=item_name,
                    quantity=quantity,
                    price_each=price_each,
                )
                .returning(MarketplaceListing)
            )
            await session.commit()
            return _result_one(result)

    async def get_listings(
        self,
        item_name: Optional[str] = None,
        page: int = 1,
        per_page: int = 15,
    ) -> Tuple[list, int]:
        """Get marketplace listings with pagination."""
        async with self.session_factory() as session:
            offset = (page - 1) * per_page

            base_stmt = (
                select(
                    MarketplaceListing.id,
                    MarketplaceListing.seller_id,
                    MarketplaceListing.item_type,
                    MarketplaceListing.item_name,
                    MarketplaceListing.quantity,
                    MarketplaceListing.price_each,
                    MarketplaceListing.created_at,
                    User.first_name,
                    User.username,
                )
                .join(User, User.user_id == MarketplaceListing.seller_id)
                .where(MarketplaceListing.quantity > 0)
            )
            count_stmt = select(func.count(MarketplaceListing.id)).where(
                MarketplaceListing.quantity > 0
            )

            if item_name:
                base_stmt = base_stmt.where(
                    MarketplaceListing.item_name == item_name
                ).order_by(MarketplaceListing.price_each.asc())
                count_stmt = count_stmt.where(
                    MarketplaceListing.item_name == item_name
                )
            else:
                base_stmt = base_stmt.order_by(
                    MarketplaceListing.created_at.desc()
                )

            result = await session.execute(
                base_stmt.limit(per_page).offset(offset)
            )
            listings = _result_all(result)
            total = await session.scalar(count_stmt)
            return listings, total

    async def get_user_listings(self, user_id: int):
        """Get all listings by a user."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(MarketplaceListing)
                .where(
                    MarketplaceListing.seller_id == user_id,
                    MarketplaceListing.quantity > 0,
                )
                .order_by(MarketplaceListing.created_at.desc())
            )
            return _result_all(result)

    async def get_listing(self, listing_id: int):
        """Get a specific listing."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(MarketplaceListing).where(
                    MarketplaceListing.id == listing_id
                )
            )
            return _result_one(result)

    async def buy_from_listing(
        self, listing_id: int, buyer_id: int, quantity: int
    ) -> Tuple[bool, str]:
        """Buy items from a listing. Returns (success, message)."""
        async with self.session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    text("""
                    SELECT * FROM marketplace_listings
                    WHERE id = :lid AND quantity >= :qty
                    FOR UPDATE
                    """),
                    {"lid": listing_id, "qty": quantity},
                )
                listing = _result_one(result)

                if not listing:
                    return False, "Listing not found or insufficient quantity"

                if listing["seller_id"] == buyer_id:
                    return False, "You cannot buy your own listing"

                total_cost = listing["price_each"] * quantity

                bal_result = await session.execute(
                    text(
                        "SELECT balance FROM wallets WHERE user_id = :uid FOR UPDATE"
                    ),
                    {"uid": buyer_id},
                )
                buyer_wallet = _result_one(bal_result)
                if not buyer_wallet or buyer_wallet["balance"] < total_cost:
                    return False, "Insufficient balance"

                await session.execute(
                    text(
                        "UPDATE wallets SET balance = balance - :cost WHERE user_id = :uid"
                    ),
                    {"cost": total_cost, "uid": buyer_id},
                )

                await session.execute(
                    text("""
                    INSERT INTO wallets (user_id, balance)
                    VALUES (:uid, :cost)
                    ON CONFLICT (user_id) DO UPDATE SET
                        balance = wallets.balance + :cost
                    """),
                    {"uid": listing["seller_id"], "cost": total_cost},
                )

                await session.execute(
                    update(MarketplaceListing)
                    .where(MarketplaceListing.id == listing_id)
                    .values(quantity=MarketplaceListing.quantity - quantity)
                )

                inv_stmt = pg_insert(Inventory).values(
                    user_id=buyer_id,
                    item_type=listing["item_type"],
                    item_name=listing["item_name"],
                    quantity=quantity,
                )
                inv_excluded = inv_stmt.excluded
                inv_stmt = inv_stmt.on_conflict_do_update(
                    index_elements=["user_id", "item_type", "item_name"],
                    set_={
                        "quantity": Inventory.quantity + inv_excluded.quantity
                    },
                )
                await session.execute(inv_stmt)

                return (
                    True,
                    f"Purchased {quantity}x {listing['item_name']} for ${total_cost:,}",
                )

    async def cancel_listing(self, listing_id: int, user_id: int) -> bool:
        """Cancel a listing and return items to seller."""
        async with self.session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    delete(MarketplaceListing)
                    .where(
                        MarketplaceListing.id == listing_id,
                        MarketplaceListing.seller_id == user_id,
                    )
                    .returning(MarketplaceListing)
                )
                listing = _result_one(result)

                if not listing:
                    return False

                inv_stmt = pg_insert(Inventory).values(
                    user_id=user_id,
                    item_type=listing["item_type"],
                    item_name=listing["item_name"],
                    quantity=listing["quantity"],
                )
                inv_excluded = inv_stmt.excluded
                inv_stmt = inv_stmt.on_conflict_do_update(
                    index_elements=["user_id", "item_type", "item_name"],
                    set_={
                        "quantity": Inventory.quantity + inv_excluded.quantity
                    },
                )
                await session.execute(inv_stmt)
                return True

    # ========== Fertilize Cooldown Methods ==========

    async def can_fertilize(self, fertilizer_id: int, target_id: int) -> bool:
        """Check if user can fertilize another user's garden."""
        from bot.constants import FERTILIZE_COOLDOWN

        async with self.session_factory() as session:
            last = await session.scalar(
                select(FertilizeCooldown.last_fertilized_at).where(
                    FertilizeCooldown.fertilizer_id == fertilizer_id,
                    FertilizeCooldown.target_id == target_id,
                )
            )
        if not last:
            return True
        return (datetime.now() - last).total_seconds() >= FERTILIZE_COOLDOWN

    async def get_fertilize_cooldown_remaining(
        self, fertilizer_id: int, target_id: int
    ):
        """Get seconds remaining on fertilize cooldown, or None if can fertilize."""
        from bot.constants import FERTILIZE_COOLDOWN

        async with self.session_factory() as session:
            last = await session.scalar(
                text("""
                SELECT last_fertilized_at FROM fertilize_cooldowns
                WHERE fertilizer_id = :fid
                ORDER BY last_fertilized_at DESC
                LIMIT 1
                """),
                {"fid": fertilizer_id},
            )
        if not last:
            return None
        elapsed = (datetime.now() - last).total_seconds()
        if elapsed >= FERTILIZE_COOLDOWN:
            return None
        return int(FERTILIZE_COOLDOWN - elapsed)

    async def record_fertilize(self, fertilizer_id: int, target_id: int):
        """Record a fertilize action."""
        async with self.session_factory() as session:
            stmt = pg_insert(FertilizeCooldown).values(
                fertilizer_id=fertilizer_id,
                target_id=target_id,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["fertilizer_id", "target_id"],
                set_={"last_fertilized_at": func.now()},
            )
            await session.execute(stmt)
            await session.execute(
                pg_insert(FertilizeLog).values(
                    fertilizer_id=fertilizer_id,
                    target_id=target_id,
                )
            )
            await session.commit()

    async def get_fertilize_ban(self, user_id: int):
        """Return banned_until datetime if user is currently banned, else None."""
        from datetime import datetime as dt

        async with self.session_factory() as session:
            row = await session.scalar(
                select(FertilizeBan.banned_until).where(
                    FertilizeBan.user_id == user_id
                )
            )
        if not row or row <= dt.now():
            return None
        return row

    async def set_fertilize_ban(
        self,
        user_id: int,
        banned_until,
        fertilize_count: int = 0,
        reason: str = "auto",
    ):
        """Upsert a fertilize ban and append an audit log row."""
        async with self.session_factory() as session:
            stmt = pg_insert(FertilizeBan).values(
                user_id=user_id,
                banned_until=banned_until,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["user_id"],
                set_={"banned_until": banned_until, "created_at": func.now()},
            )
            await session.execute(stmt)
            await session.execute(
                pg_insert(FertilizeBanLog).values(
                    user_id=user_id,
                    banned_until=banned_until,
                    fertilize_count=fertilize_count,
                    reason=reason,
                )
            )
            await session.commit()

    async def count_recent_fertilizes(
        self, user_id: int, window_hours: int = 3
    ) -> int:
        """Count fertilize actions by user in the last window_hours hours."""
        from datetime import datetime as dt, timedelta as td

        cutoff = dt.now() - td(hours=window_hours)
        async with self.session_factory() as session:
            result = await session.scalar(
                text("""
                SELECT COUNT(*) FROM transactions
                WHERE user_id = :uid
                  AND reason ILIKE 'Fertilized%garden'
                  AND created_at > :cutoff
                """),
                {"uid": user_id, "cutoff": cutoff},
            )
        return int(result or 0)

    async def get_fertilize_receive_ban(self, user_id: int):
        """Return banned_until datetime if user cannot receive fertilizes, else None."""
        from datetime import datetime as dt

        async with self.session_factory() as session:
            row = await session.scalar(
                select(FertilizeReceiveBan.banned_until).where(
                    FertilizeReceiveBan.user_id == user_id
                )
            )
        if not row or row <= dt.now():
            return None
        return row

    async def set_fertilize_receive_ban(
        self,
        user_id: int,
        banned_until,
        fertilize_count: int = 0,
        reason: str = "auto",
    ):
        """Upsert a receive-ban and append an audit log row."""
        async with self.session_factory() as session:
            stmt = pg_insert(FertilizeReceiveBan).values(
                user_id=user_id,
                banned_until=banned_until,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["user_id"],
                set_={"banned_until": banned_until, "created_at": func.now()},
            )
            await session.execute(stmt)
            await session.execute(
                pg_insert(FertilizeReceiveBanLog).values(
                    user_id=user_id,
                    banned_until=banned_until,
                    fertilize_count=fertilize_count,
                    reason=reason,
                )
            )
            await session.commit()

    async def get_recent_fertilize_target_stats(
        self, fertilizer_id: int, window_hours: int = 3
    ):
        """Return total count and top target stats for a fertilizer in the window."""
        from datetime import datetime as dt, timedelta as td

        cutoff = dt.now() - td(hours=window_hours)
        async with self.session_factory() as session:
            total = await session.scalar(
                text("""
                SELECT COUNT(*) FROM fertilize_log
                WHERE fertilizer_id = :uid
                  AND created_at > :cutoff
                """),
                {"uid": fertilizer_id, "cutoff": cutoff},
            )
            result = await session.execute(
                text("""
                SELECT target_id, COUNT(*) AS cnt FROM fertilize_log
                WHERE fertilizer_id = :uid
                  AND created_at > :cutoff
                GROUP BY target_id
                ORDER BY cnt DESC
                LIMIT 1
                """),
                {"uid": fertilizer_id, "cutoff": cutoff},
            )
            row = result.first()
        if not row:
            return int(total or 0), None, 0
        return (
            int(total or 0),
            row._mapping["target_id"],
            int(row._mapping["cnt"]),
        )

    async def get_recent_fertilize_target_breakdown(
        self, fertilizer_id: int, window_hours: int = 5
    ) -> list[dict]:
        """Return per-target fertilize counts for a fertilizer in the window."""
        from datetime import datetime as dt, timedelta as td

        cutoff = dt.now() - td(hours=window_hours)
        async with self.session_factory() as session:
            result = await session.execute(
                text("""
                SELECT fl.target_id,
                       COUNT(*) AS cnt,
                       u.username,
                       u.first_name
                FROM fertilize_log fl
                LEFT JOIN users u ON u.user_id = fl.target_id
                WHERE fl.fertilizer_id = :uid
                  AND fl.created_at > :cutoff
                GROUP BY fl.target_id, u.username, u.first_name
                ORDER BY cnt DESC, fl.target_id ASC
                """),
                {"uid": fertilizer_id, "cutoff": cutoff},
            )
            rows = result.fetchall()
        return [dict(r._mapping) for r in rows]

    async def set_auto_harvest(
        self, owner_id: int, enabled: bool, chat_id: int
    ):
        """Enable or disable auto-harvest for a garden, storing notification chat."""
        async with self.session_factory() as session:
            await session.execute(
                text(
                    "UPDATE gardens SET auto_harvest = :enabled, notify_chat_id = :chat_id "
                    "WHERE owner_id = :uid"
                ),
                {"enabled": enabled, "chat_id": chat_id, "uid": owner_id},
            )
            await session.commit()

    async def get_auto_harvest_gardens(self) -> list:
        """Return all gardens with auto_harvest enabled."""
        async with self.session_factory() as session:
            rows = await session.execute(
                text(
                    "SELECT id, owner_id, notify_chat_id FROM gardens "
                    "WHERE auto_harvest = TRUE AND notify_chat_id IS NOT NULL"
                )
            )
            return [dict(r._mapping) for r in rows]

    async def update_notify_chat(self, owner_id: int, chat_id: int):
        """Update the notification chat for a garden (called on /plant)."""
        async with self.session_factory() as session:
            await session.execute(
                text(
                    "UPDATE gardens SET notify_chat_id = :cid WHERE owner_id = :uid"
                ),
                {"cid": chat_id, "uid": owner_id},
            )
            await session.commit()

    # ========== Hire Request Methods ==========

    async def create_hire_request(
        self,
        factory_id: int,
        worker_user_id: int,
        requester_id: int,
        chat_id: int,
        message_id: Optional[int] = None,
    ):
        """Create a hire request pending worker approval."""
        async with self.session_factory() as session:
            try:
                result = await session.execute(
                    insert(HireRequest)
                    .values(
                        factory_id=factory_id,
                        worker_user_id=worker_user_id,
                        requester_id=requester_id,
                        chat_id=chat_id,
                        message_id=message_id,
                    )
                    .returning(HireRequest)
                )
                await session.commit()
                return _result_one(result)
            except Exception:
                return None

    async def get_hire_request(self, factory_id: int, worker_user_id: int):
        """Get a pending hire request."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(HireRequest).where(
                    HireRequest.factory_id == factory_id,
                    HireRequest.worker_user_id == worker_user_id,
                )
            )
            return _result_one(result)

    async def get_hire_request_by_id(self, request_id: int):
        """Get a hire request by ID."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(HireRequest).where(HireRequest.id == request_id)
            )
            return _result_one(result)

    async def delete_hire_request(self, request_id: int) -> bool:
        """Delete a hire request."""
        async with self.session_factory() as session:
            result = await session.execute(
                delete(HireRequest).where(HireRequest.id == request_id)
            )
            await session.commit()
            return result.rowcount > 0

    async def get_worker_factories(self, worker_id: int):
        """Get all factories a worker is assigned to."""
        async with self.session_factory() as session:
            result = await session.execute(
                text("""
                SELECT f.*, wa.fatigue, wa.is_working, wa.id as assignment_id,
                       u.first_name as owner_name
                FROM worker_assignments wa
                JOIN factories f ON wa.factory_id = f.id
                JOIN users u ON f.owner_id = u.user_id
                WHERE wa.worker_id = :wid
                """),
                {"wid": worker_id},
            )
            return _result_all(result)

    async def resign_from_factory(
        self, worker_id: int, factory_id: int
    ) -> dict:
        """
        Remove worker from a factory (self-resign).
        Returns dict with 'success' and 'earnings_given_to_owner'.
        """
        from bot.constants import FACTORY_BASE_EARNING

        async with self.session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    text("""
                    SELECT wa.*, f.owner_id
                    FROM worker_assignments wa
                    JOIN factories f ON f.id = wa.factory_id
                    WHERE wa.worker_id = :wid AND wa.factory_id = :fid
                    """),
                    {"wid": worker_id, "fid": factory_id},
                )
                assignment = _result_one(result)

                if not assignment:
                    return {"success": False, "reason": "not_assigned"}

                earnings_to_owner = 0

                if assignment["is_working"] and assignment.get(
                    "work_started_at"
                ):
                    earnings_to_owner = FACTORY_BASE_EARNING
                    await session.execute(
                        update(Factory)
                        .where(Factory.id == factory_id)
                        .values(
                            total_earnings=Factory.total_earnings
                            + earnings_to_owner
                        )
                    )

                await session.execute(
                    delete(WorkerAssignment).where(
                        WorkerAssignment.worker_id == worker_id,
                        WorkerAssignment.factory_id == factory_id,
                    )
                )

                return {
                    "success": True,
                    "earnings_given_to_owner": earnings_to_owner,
                }

    async def is_hire_blocked(self, user_id: int, blocked_by: int) -> bool:
        """Check if user is blocked from being hired by someone."""
        async with self.session_factory() as session:
            return bool(
                await session.scalar(
                    select(
                        exists().where(
                            and_(
                                HireBlock.user_id == blocked_by,
                                HireBlock.blocked_user_id == user_id,
                            )
                        )
                    )
                )
            )

    async def add_hire_block(self, user_id: int, blocked_user_id: int):
        """Block someone from hiring you."""
        async with self.session_factory() as session:
            await session.execute(
                pg_insert(HireBlock)
                .values(user_id=user_id, blocked_user_id=blocked_user_id)
                .on_conflict_do_nothing()
            )
            await session.commit()

    async def remove_hire_block(
        self, user_id: int, blocked_user_id: int
    ) -> bool:
        """Remove hire block."""
        async with self.session_factory() as session:
            result = await session.execute(
                delete(HireBlock).where(
                    HireBlock.user_id == user_id,
                    HireBlock.blocked_user_id == blocked_user_id,
                )
            )
            await session.commit()
            return result.rowcount > 0

    # ========== Sonar Game Methods ==========

    async def create_sonar_game(
        self,
        user_id: int,
        bet_amount: int,
        chest_positions: List[int],
        chat_id: int,
        message_id: Optional[int] = None,
    ):
        """Create a new sonar game."""
        if chest_positions is None:
            chest_positions = []

        if isinstance(chest_positions, str):
            try:
                chest_positions = json.loads(chest_positions)
            except Exception:
                chest_positions = []

        if isinstance(chest_positions, (list, tuple)) and any(
            isinstance(p, (list, tuple)) for p in chest_positions
        ):
            flat: list = []
            for p in chest_positions:
                if isinstance(p, (list, tuple)) and len(p) >= 2:
                    flat.append(int(p[0]))
                    flat.append(int(p[1]))
                else:
                    flat.append(int(p))
            chest_positions = flat

        chest_positions = [int(x) for x in chest_positions]

        async with self.session_factory() as session:
            result = await session.execute(
                insert(SonarGame)
                .values(
                    user_id=user_id,
                    bet_amount=bet_amount,
                    chest_positions=chest_positions,
                    chat_id=chat_id,
                    message_id=message_id,
                )
                .returning(SonarGame)
            )
            await session.commit()
            return _result_one(result)

    async def get_active_sonar_game(self, chat_id: int):
        """Get active sonar game in a chat."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(SonarGame)
                .where(
                    SonarGame.chat_id == chat_id, SonarGame.is_active == True
                )  # noqa: E712
                .order_by(SonarGame.created_at.desc())
                .limit(1)
            )
            return _result_one(result)

    async def update_sonar_game(
        self,
        game_id: int,
        revealed_cells: Optional[List[int]] = None,
        chests_found: Optional[int] = None,
        total_guesses: Optional[int] = None,
        is_active: Optional[bool] = None,
        **kwargs,
    ):
        """Update sonar game state."""
        guesses = kwargs.get("guesses")
        found_positions = kwargs.get("found_positions")
        completed = kwargs.get("completed")

        if is_active is None:
            if completed is not None:
                is_active = not bool(completed)
            else:
                is_active = True

        set_clauses: list = []
        params: dict = {"game_id": game_id}

        if revealed_cells is not None:
            set_clauses.append("revealed_cells = :revealed_cells")
            params["revealed_cells"] = revealed_cells

        if chests_found is not None:
            set_clauses.append("chests_found = :chests_found")
            params["chests_found"] = chests_found

        if total_guesses is not None:
            set_clauses.append("total_guesses = :total_guesses")
            params["total_guesses"] = total_guesses

        if guesses is not None:
            try:
                guesses_json = json.dumps(guesses)
            except Exception:
                guesses_json = json.dumps({})
            set_clauses.append("guesses = CAST(:guesses AS jsonb)")
            params["guesses"] = guesses_json

        if found_positions is not None:
            flat_fp: list = []
            if isinstance(found_positions, str):
                try:
                    fp = json.loads(found_positions)
                except Exception:
                    fp = []
            else:
                fp = found_positions

            if isinstance(fp, (list, tuple)) and fp:
                if all(isinstance(x, int) for x in fp):
                    flat_fp = [int(x) for x in fp]
                else:
                    for p in fp:
                        if isinstance(p, (list, tuple)) and len(p) >= 2:
                            flat_fp.append(int(p[0]))
                            flat_fp.append(int(p[1]))
                        elif isinstance(p, str):
                            parts = p.split(",")
                            if len(parts) >= 2:
                                try:
                                    flat_fp.append(int(parts[0]))
                                    flat_fp.append(int(parts[1]))
                                except Exception:
                                    continue
            set_clauses.append("found_positions = :found_positions")
            params["found_positions"] = flat_fp

        if is_active is not None:
            set_clauses.append("is_active = :is_active")
            params["is_active"] = is_active

        if not set_clauses:
            return

        set_sql = ", ".join(set_clauses)
        sql = f"UPDATE sonar_games SET {set_sql} WHERE id = :game_id"

        try:
            if guesses is not None:
                async with self.engine.begin() as alt_conn:
                    await alt_conn.execute(
                        text(
                            "ALTER TABLE sonar_games ADD COLUMN IF NOT EXISTS guesses JSONB DEFAULT '{}'::jsonb"
                        )
                    )
            if found_positions is not None:
                async with self.engine.begin() as alt_conn:
                    await alt_conn.execute(
                        text(
                            "ALTER TABLE sonar_games ADD COLUMN IF NOT EXISTS found_positions INTEGER[] DEFAULT '{}'"
                        )
                    )
        except Exception:
            pass

        async with self.session_factory() as session:
            await session.execute(text(sql), params)
            await session.commit()

    async def end_sonar_game(self, game_id: int):
        """End a sonar game."""
        async with self.session_factory() as session:
            await session.execute(
                update(SonarGame)
                .where(SonarGame.id == game_id)
                .values(is_active=False)
            )
            await session.commit()

    # ========== Nation Game Methods ==========

    async def create_nation_game(
        self, chat_id: int, nation_name: str, photo_b64: Optional[str] = None
    ):
        """Create or replace a nation game for a chat."""
        async with self.session_factory() as session:
            stmt = pg_insert(NationGame).values(
                chat_id=chat_id, nation_name=nation_name, photo_b64=photo_b64
            )
            excluded = stmt.excluded
            stmt = stmt.on_conflict_do_update(
                index_elements=["chat_id"],
                set_={
                    "nation_name": excluded.nation_name,
                    "photo_b64": excluded.photo_b64,
                    "created_at": func.now(),
                },
            ).returning(NationGame)
            result = await session.execute(stmt)
            await session.commit()
            return _result_one(result)

    async def get_nation_game(self, chat_id: int):
        """Get active nation game in a chat."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(NationGame).where(NationGame.chat_id == chat_id)
            )
            return _result_one(result)

    async def delete_nation_game(self, chat_id: int) -> bool:
        """Delete nation game for a chat."""
        async with self.session_factory() as session:
            result = await session.execute(
                delete(NationGame).where(NationGame.chat_id == chat_id)
            )
            await session.commit()
            return result.rowcount > 0

    # ========== Fishing Methods ==========

    async def get_fishing_stats(self, user_id: int):
        """Get or create fishing stats for user."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(FishingStat).where(FishingStat.user_id == user_id)
            )
            stats = _result_one(result)
            if stats:
                return stats
            stmt = (
                insert(FishingStat)
                .values(user_id=user_id)
                .returning(FishingStat)
            )
            result = await session.execute(stmt)
            await session.commit()
            return _result_one(result)

    async def update_fishing_stats(
        self,
        user_id: int,
        bait_change: int = 0,
        caught: int = 0,
        earned: int = 0,
        biggest_catch: Optional[str] = None,
    ):
        """Update fishing stats."""
        async with self.session_factory() as session:
            values: dict = {
                "bait_count": FishingStat.bait_count + bait_change,
                "total_caught": FishingStat.total_caught + caught,
                "total_earned": FishingStat.total_earned + earned,
            }
            if biggest_catch:
                values["biggest_catch"] = biggest_catch
            await session.execute(
                update(FishingStat)
                .where(FishingStat.user_id == user_id)
                .values(**values)
            )
            await session.commit()

    async def add_fish(self, user_id: int, fish_type: str, quantity: int = 1):
        """Add fish to user's fishing inventory."""
        async with self.session_factory() as session:
            stmt = pg_insert(FishingInventory).values(
                user_id=user_id, fish_type=fish_type, quantity=quantity
            )
            excluded = stmt.excluded
            stmt = stmt.on_conflict_do_update(
                index_elements=["user_id", "fish_type"],
                set_={
                    "quantity": FishingInventory.quantity + excluded.quantity
                },
            )
            await session.execute(stmt)
            await session.commit()

    async def get_fish_inventory(self, user_id: int) -> dict:
        """Get user's fishing inventory as dict {fish_type: quantity}."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(FishingInventory.fish_type, FishingInventory.quantity)
                .where(
                    FishingInventory.user_id == user_id,
                    FishingInventory.quantity > 0,
                )
                .order_by(FishingInventory.fish_type)
            )
            rows = result.fetchall()
            return {row[0]: row[1] for row in rows}

    async def remove_fish(
        self, user_id: int, fish_type: str, quantity: int
    ) -> bool:
        """Remove fish from inventory."""
        async with self.session_factory() as session:
            result = await session.execute(
                update(FishingInventory)
                .where(
                    FishingInventory.user_id == user_id,
                    FishingInventory.fish_type == fish_type,
                    FishingInventory.quantity >= quantity,
                )
                .values(quantity=FishingInventory.quantity - quantity)
            )
            await session.commit()
            return result.rowcount > 0

    async def use_bait(self, user_id: int, amount: int = 1) -> bool:
        """Use bait for fishing."""
        async with self.session_factory() as session:
            result = await session.execute(
                update(FishingStat)
                .where(
                    FishingStat.user_id == user_id,
                    FishingStat.bait_count >= amount,
                )
                .values(bait_count=FishingStat.bait_count - amount)
            )
            await session.commit()
            return result.rowcount > 0

    async def use_bait_bulk(self, user_id: int, amount: int) -> bool:
        """Use bait in bulk (alias for use_bait with amount)."""
        return await self.use_bait(user_id, amount)

    async def add_bait(self, user_id: int, amount: int):
        """Add bait to user's inventory."""
        await self.get_fishing_stats(user_id)  # Ensure record exists
        async with self.session_factory() as session:
            await session.execute(
                update(FishingStat)
                .where(FishingStat.user_id == user_id)
                .values(bait_count=FishingStat.bait_count + amount)
            )
            await session.commit()

    async def add_fishing_stat(self, user_id: int, stat: str, value: int):
        """Add to a specific fishing stat."""
        await self.get_fishing_stats(user_id)  # Ensure record exists
        async with self.session_factory() as session:
            if stat == "total_caught":
                await session.execute(
                    update(FishingStat)
                    .where(FishingStat.user_id == user_id)
                    .values(total_caught=FishingStat.total_caught + value)
                )
            elif stat == "total_sold":
                await session.execute(
                    update(FishingStat)
                    .where(FishingStat.user_id == user_id)
                    .values(total_earned=FishingStat.total_earned + value)
                )
            await session.commit()

    async def get_daily_fish_count(self, user_id: int) -> int:
        """Get user's daily fish count, resetting at UTC midnight."""
        from datetime import datetime, timezone

        today_utc = datetime.now(timezone.utc).date()
        async with self.session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    text(
                        "SELECT daily_fish_count, daily_fish_date FROM fishing_stats WHERE user_id = :uid"
                    ),
                    {"uid": user_id},
                )
                row = _result_one(result)
                if not row:
                    return 0
                if (
                    row["daily_fish_date"] is None
                    or row["daily_fish_date"] != today_utc
                ):
                    await session.execute(
                        text(
                            "UPDATE fishing_stats SET daily_fish_count = 0, daily_fish_date = :dt WHERE user_id = :uid"
                        ),
                        {"uid": user_id, "dt": today_utc},
                    )
                    return 0
                return row["daily_fish_count"]

    async def increment_daily_fish_count(self, user_id: int, amount: int):
        """Increment user's daily fish count."""
        from datetime import datetime, timezone

        today_utc = datetime.now(timezone.utc).date()
        async with self.session_factory() as session:
            await session.execute(
                text("""
                INSERT INTO fishing_stats (user_id, daily_fish_count, daily_fish_date)
                VALUES (:uid, :amt, :dt)
                ON CONFLICT (user_id) DO UPDATE SET
                    daily_fish_count = fishing_stats.daily_fish_count + :amt,
                    daily_fish_date = :dt
                """),
                {"uid": user_id, "amt": amount, "dt": today_utc},
            )
            await session.commit()

    async def get_utc_midnight_seconds_remaining(self) -> int:
        """Get seconds remaining until next UTC midnight."""
        from datetime import datetime, timedelta, timezone

        now_utc = datetime.now(timezone.utc)
        next_midnight = (now_utc + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return int((next_midnight - now_utc).total_seconds())

    # ========== Job Methods ==========

    async def get_job(self, user_id: int):
        """Get user's job."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(Job).where(Job.user_id == user_id)
            )
            return _result_one(result)

    async def get_user_job(self, user_id: int):
        """Backward-compatible alias for get_job."""
        return await self.get_job(user_id)

    async def join_job(self, user_id: int, job_type: str):
        """Join a job. Restores XP from job_skills if previously done this job."""
        async with self.session_factory() as session:
            async with session.begin():
                saved_result = await session.execute(
                    text(
                        "SELECT job_xp, job_level FROM job_skills WHERE user_id = :uid AND job_type = :jtype"
                    ),
                    {"uid": user_id, "jtype": job_type},
                )
                saved = _result_one(saved_result)
                saved_xp = saved["job_xp"] if saved else 0
                saved_level = saved["job_level"] if saved else 1

                result = await session.execute(
                    text("""
                    INSERT INTO jobs (user_id, job_type, job_xp, job_level)
                    VALUES (:uid, :jtype, :xp, :level)
                    ON CONFLICT (user_id) DO UPDATE SET
                        job_type = :jtype, job_xp = :xp, job_level = :level, stats = '{}'::jsonb
                    RETURNING *
                    """),
                    {
                        "uid": user_id,
                        "jtype": job_type,
                        "xp": saved_xp,
                        "level": saved_level,
                    },
                )
                return _result_one(result)

    async def quit_job(self, user_id: int) -> bool:
        """Quit current job. Saves XP to job_skills for later restoration."""
        async with self.session_factory() as session:
            async with session.begin():
                job_result = await session.execute(
                    select(Job.job_type, Job.job_xp, Job.job_level).where(
                        Job.user_id == user_id
                    )
                )
                job = _result_one(job_result)
                if job:
                    skill_stmt = pg_insert(JobSkill).values(
                        user_id=user_id,
                        job_type=job["job_type"],
                        job_xp=job["job_xp"],
                        job_level=job["job_level"],
                    )
                    skill_excluded = skill_stmt.excluded
                    skill_stmt = skill_stmt.on_conflict_do_update(
                        index_elements=["user_id", "job_type"],
                        set_={
                            "job_xp": skill_excluded.job_xp,
                            "job_level": skill_excluded.job_level,
                        },
                    )
                    await session.execute(skill_stmt)

                result = await session.execute(
                    delete(Job).where(Job.user_id == user_id)
                )
                return result.rowcount > 0

    async def update_job_xp(self, user_id: int, xp: int):
        """Add XP to job and recalculate level."""
        from bot.constants import get_job_level

        async with self.session_factory() as session:
            async with session.begin():
                current_xp = await session.scalar(
                    select(Job.job_xp).where(Job.user_id == user_id)
                )
                if current_xp is not None:
                    new_xp = current_xp + xp
                    new_level = get_job_level(new_xp)
                    await session.execute(
                        update(Job)
                        .where(Job.user_id == user_id)
                        .values(job_xp=new_xp, job_level=new_level)
                    )

    async def set_work_cooldown(self, user_id: int):
        """Set universal work cooldown timestamp."""
        async with self.session_factory() as session:
            stmt = pg_insert(WorkCooldown).values(
                user_id=user_id, last_work_at=func.now()
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["user_id"],
                set_={"last_work_at": func.now()},
            )
            await session.execute(stmt)
            await session.commit()

    async def get_work_cooldown(self, user_id: int):
        """Get universal work cooldown timestamp."""
        async with self.session_factory() as session:
            return await session.scalar(
                select(WorkCooldown.last_work_at).where(
                    WorkCooldown.user_id == user_id
                )
            )

    async def get_last_crime_timestamp(self, user_id: int):
        """Get timestamp of user's last crime."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(CrimeLog.created_at)
                .where(CrimeLog.criminal_id == user_id)
                .order_by(CrimeLog.created_at.desc())
                .limit(1)
            )
            row = result.fetchone()
            return row[0] if row else None

    async def is_in_passive_mode(self, user_id: int, days: int = 5):
        """Check if user is in passive mode (no crime activity for N days)."""
        last_crime = await self.get_last_crime_timestamp(user_id)
        if not last_crime:
            return True

        from datetime import datetime, timedelta

        now = datetime.now()
        if last_crime.tzinfo is not None:
            last_crime = last_crime.replace(tzinfo=None)

        return (now - last_crime) > timedelta(days=days)

    async def get_recent_crimes(
        self, user_id: Optional[int] = None, hours: int = 3
    ):
        """Get recent crimes (optionally filtered by user)."""
        async with self.session_factory() as session:
            if user_id:
                result = await session.execute(
                    text("""
                    SELECT * FROM crime_log
                    WHERE criminal_id = :uid
                        AND created_at > NOW() - make_interval(hours => :hours)
                    ORDER BY created_at DESC
                    """),
                    {"uid": user_id, "hours": hours},
                )
            else:
                result = await session.execute(
                    text("""
                    SELECT * FROM crime_log
                    WHERE created_at > NOW() - make_interval(hours => :hours)
                    ORDER BY created_at DESC
                    """),
                    {"hours": hours},
                )
            return _result_all(result)

    async def user_has_crime_history(self, user_id: int) -> bool:
        """Check if user has any criminal history."""
        async with self.session_factory() as session:
            has_crimes = await session.scalar(
                select(
                    exists().where(
                        and_(
                            CrimeLog.criminal_id == user_id,
                            CrimeLog.crime_type.in_([
                                "rob",
                                "kill",
                                "heist",
                                "gangwar",
                            ]),
                        )
                    )
                )
            )
            if has_crimes:
                return True
            job_result = await session.execute(
                select(Job.job_type).where(Job.user_id == user_id)
            )
            job = _result_one(job_result)
            if job and job["job_type"] in ("gangster", "thief"):
                return True
            return False

    # ========== Achievement Methods ==========

    async def get_achievements(self, user_id: int):
        """Get all achievements for a user."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(Achievement)
                .where(Achievement.user_id == user_id)
                .order_by(Achievement.unlocked_at.desc())
            )
            return _result_all(result)

    async def has_achievement(self, user_id: int, achievement_key: str) -> bool:
        """Check if user has an achievement."""
        async with self.session_factory() as session:
            return bool(
                await session.scalar(
                    select(
                        exists().where(
                            and_(
                                Achievement.user_id == user_id,
                                Achievement.achievement_key == achievement_key,
                            )
                        )
                    )
                )
            )

    async def unlock_achievement(
        self, user_id: int, achievement_key: str
    ) -> bool:
        """Unlock an achievement. Returns True if newly unlocked."""
        async with self.session_factory() as session:
            try:
                await session.execute(
                    insert(Achievement).values(
                        user_id=user_id, achievement_key=achievement_key
                    )
                )
                await session.commit()
                return True
            except Exception:
                return False

    # ========== Combat/Hearts Methods ==========

    async def get_user_hearts(self, user_id: int) -> int:
        """Get user's current hearts (3 max, 0 = dead)."""
        async with self.session_factory() as session:
            result = await session.scalar(
                select(User.hearts).where(User.user_id == user_id)
            )
            return result if result is not None else 3

    async def set_user_hearts(self, user_id: int, hearts: int) -> int:
        """Set user's hearts. Returns new value."""
        hearts = max(0, min(3, hearts))
        async with self.session_factory() as session:
            await session.execute(
                update(User)
                .where(User.user_id == user_id)
                .values(hearts=hearts)
            )
            await session.commit()
        return hearts

    async def remove_heart(self, user_id: int) -> int:
        """Remove one heart from user. Returns new heart count."""
        async with self.session_factory() as session:
            result = await session.execute(
                update(User)
                .where(User.user_id == user_id)
                .values(hearts=func.greatest(User.hearts - 1, 0))
                .returning(User.hearts)
            )
            await session.commit()
            row = result.fetchone()
            return row[0] if row else 0

    async def restore_heart(self, user_id: int) -> int:
        """Restore one heart to user. Returns new heart count."""
        async with self.session_factory() as session:
            result = await session.execute(
                update(User)
                .where(User.user_id == user_id)
                .values(hearts=func.least(User.hearts + 1, 3))
                .returning(User.hearts)
            )
            await session.commit()
            row = result.fetchone()
            return row[0] if row else 3

    async def is_user_dead(self, user_id: int) -> bool:
        """Check if user has 0 hearts (dead)."""
        hearts = await self.get_user_hearts(user_id)
        return hearts <= 0

    async def log_crime(
        self,
        user_id: int,
        action_type: str,
        target_id: Optional[int] = None,
        success: bool = False,
        amount: int = 0,
    ):
        """Log a criminal action."""
        async with self.session_factory() as session:
            await session.execute(
                insert(CrimeLog).values(
                    criminal_id=user_id,
                    crime_type=action_type,
                    victim_id=target_id,
                    is_solved=not success,
                    amount=amount,
                )
            )
            await session.commit()

    async def get_crime_history(self, user_id: int, limit: int = 10):
        """Get user's crime history."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(CrimeLog)
                .where(CrimeLog.criminal_id == user_id)
                .order_by(CrimeLog.created_at.desc())
                .limit(limit)
            )
            return _result_all(result)

    async def has_criminal_record(self, user_id: int) -> bool:
        """Check if user has any criminal history."""
        async with self.session_factory() as session:
            has_crimes = await session.scalar(
                select(
                    exists().where(
                        and_(
                            CrimeLog.criminal_id == user_id,
                            CrimeLog.crime_type.in_([
                                "rob",
                                "kill",
                                "heist",
                                "gangwar",
                            ]),
                        )
                    )
                )
            )
            if has_crimes:
                return True

            job_result = await session.execute(
                select(Job.job_type).where(Job.user_id == user_id)
            )
            job = _result_one(job_result)
            if job and job["job_type"] in ("thief", "gangster"):
                return True

            return False

    async def has_recent_crimes(self, user_id: int, hours: int = 6) -> bool:
        """Check if user has committed crimes in the last N hours."""
        async with self.session_factory() as session:
            return bool(
                await session.scalar(
                    text("""
                    SELECT EXISTS(
                        SELECT 1 FROM crime_log
                        WHERE criminal_id = :uid
                        AND crime_type IN ('rob', 'kill', 'heist', 'gangwar')
                        AND created_at > NOW() - INTERVAL '1 hour' * :hours
                    )
                    """),
                    {"uid": user_id, "hours": hours},
                )
            )

    async def has_recent_unsolved_crimes(
        self, user_id: int, hours: int = 6
    ) -> bool:
        """Check if user has unsolved crimes in the last N hours."""
        async with self.session_factory() as session:
            return bool(
                await session.scalar(
                    text("""
                    SELECT EXISTS(
                        SELECT 1 FROM crime_log
                        WHERE criminal_id = :uid
                        AND crime_type IN ('rob', 'kill', 'heist', 'gangwar')
                        AND is_solved = FALSE
                        AND created_at > NOW() - INTERVAL '1 hour' * :hours
                    )
                    """),
                    {"uid": user_id, "hours": hours},
                )
            )

    async def get_daily_action_count(
        self, user_id: int, action_type: str, target_id: Optional[int] = None
    ) -> int:
        """Get count of actions done today."""
        async with self.session_factory() as session:
            if target_id:
                return await session.scalar(
                    text("""
                    SELECT COALESCE(SUM(count), 0) FROM daily_actions
                    WHERE user_id = :uid AND action_type = :atype
                    AND target_id = :tid AND action_date = CURRENT_DATE
                    """),
                    {"uid": user_id, "atype": action_type, "tid": target_id},
                )
            return await session.scalar(
                text("""
                SELECT COALESCE(SUM(count), 0) FROM daily_actions
                WHERE user_id = :uid AND action_type = :atype AND action_date = CURRENT_DATE
                """),
                {"uid": user_id, "atype": action_type},
            )

    async def increment_daily_action(
        self, user_id: int, action_type: str, target_id: Optional[int] = None
    ):
        """Increment daily action count."""
        async with self.session_factory() as session:
            await session.execute(
                text("""
                INSERT INTO daily_actions (user_id, action_type, target_id, action_date, count)
                VALUES (:uid, :atype, :tid, CURRENT_DATE, 1)
                ON CONFLICT (user_id, action_type, target_id, action_date)
                DO UPDATE SET count = daily_actions.count + 1
                """),
                {"uid": user_id, "atype": action_type, "tid": target_id},
            )
            await session.commit()

    # ========== Leaderboard Methods ==========

    async def get_leaderboard_money(self, limit: int = 10):
        """Get top users by balance."""
        async with self.session_factory() as session:
            result = await session.execute(
                text("""
                SELECT u.user_id, u.first_name, w.balance
                FROM wallets w
                JOIN users u ON w.user_id = u.user_id
                ORDER BY w.balance DESC
                LIMIT :lim
                """),
                {"lim": limit},
            )
            return _result_all(result)

    async def get_leaderboard_friends(self, limit: int = 10):
        """Get users with most friends."""
        async with self.session_factory() as session:
            result = await session.execute(
                text("""
                SELECT u.user_id, u.first_name,
                       COUNT(*) as friend_count
                FROM users u
                JOIN friendships f ON u.user_id = f.user1_id OR u.user_id = f.user2_id
                GROUP BY u.user_id, u.first_name
                ORDER BY friend_count DESC
                LIMIT :lim
                """),
                {"lim": limit},
            )
            return _result_all(result)

    async def get_leaderboard_family(self, limit: int = 10):
        """Get users with largest family trees."""
        async with self.session_factory() as session:
            result = await session.execute(
                text("""
                SELECT u.user_id, u.first_name,
                       (SELECT COUNT(*) FROM family_relationships
                        WHERE parent_id = u.user_id OR child_id = u.user_id) +
                       (SELECT COUNT(*) FROM marriages
                        WHERE user1_id = u.user_id OR user2_id = u.user_id) as family_size
                FROM users u
                ORDER BY family_size DESC
                LIMIT :lim
                """),
                {"lim": limit},
            )
            return _result_all(result)

    async def get_leaderboard_gambling(self, limit: int = 10):
        """Get users with most gambling wins."""
        async with self.session_factory() as session:
            result = await session.execute(
                text("""
                SELECT u.user_id, u.first_name, SUM(gs.total_won) as total_won
                FROM users u
                JOIN gambling_stats gs ON u.user_id = gs.user_id
                GROUP BY u.user_id, u.first_name
                ORDER BY total_won DESC
                LIMIT :lim
                """),
                {"lim": limit},
            )
            return _result_all(result)

    async def get_leaderboard_factory(self, limit: int = 10):
        """Get users with most factory earnings."""
        async with self.session_factory() as session:
            result = await session.execute(
                text("""
                SELECT u.user_id, u.first_name, f.total_earnings
                FROM factories f
                JOIN users u ON f.owner_id = u.user_id
                ORDER BY f.total_earnings DESC
                LIMIT :lim
                """),
                {"lim": limit},
            )
            return _result_all(result)

    # ============ JAIL SYSTEM ============

    async def jail_user(self, user_id: int, jailed_by: int, reason: str = ""):
        """Jail a user."""
        try:
            async with self.session_factory() as session:
                stmt = pg_insert(Jail).values(
                    user_id=user_id,
                    jailed_by=jailed_by,
                    reason=reason,
                    release_at=text("NOW() + INTERVAL '24 hours'"),
                )
                excluded = stmt.excluded
                stmt = stmt.on_conflict_do_update(
                    index_elements=["user_id"],
                    set_={
                        "jailed_by": excluded.jailed_by,
                        "reason": excluded.reason,
                        "jailed_at": func.now(),
                        "release_at": text("NOW() + INTERVAL '24 hours'"),
                    },
                )
                await session.execute(stmt)
                await session.commit()
        except Exception:
            pass

    async def is_user_jailed(self, user_id: int) -> bool:
        """Check if a user is currently jailed."""
        try:
            async with self.session_factory() as session:
                return bool(
                    await session.scalar(
                        select(
                            exists().where(
                                and_(
                                    Jail.user_id == user_id,
                                    Jail.release_at > func.now(),
                                )
                            )
                        )
                    )
                )
        except Exception:
            return False

    async def get_jail_info(self, user_id: int):
        """Get jail info for a user."""
        try:
            async with self.session_factory() as session:
                result = await session.execute(
                    select(Jail).where(
                        Jail.user_id == user_id, Jail.release_at > func.now()
                    )
                )
                return _result_one(result)
        except Exception:
            return None

    async def release_user(self, user_id: int):
        """Release a user from jail."""
        try:
            async with self.session_factory() as session:
                await session.execute(
                    delete(Jail).where(Jail.user_id == user_id)
                )
                await session.commit()
        except Exception:
            pass

    async def can_use_crime_commands(self, user_id: int) -> tuple:
        """Check if user can use crime commands (kill/rob).
        Returns (can_use, reason_if_blocked)
        """
        jail_info = await self.get_jail_info(user_id)
        if jail_info:
            release_at = jail_info["release_at"]
            if release_at.tzinfo is not None:
                release_at = release_at.replace(tzinfo=None)
            from datetime import datetime

            time_left = release_at - datetime.now()
            hours = int(time_left.total_seconds()) // 3600
            minutes = (int(time_left.total_seconds()) % 3600) // 60

            jailed_by_id = jail_info["jailed_by"]
            jailed_by_user = await self.get_user(jailed_by_id)
            jailed_by_name = (
                jailed_by_user["first_name"] if jailed_by_user else "Unknown"
            )

            return (
                False,
                f"🔒 Jailed by {jailed_by_name}! Release in {hours}h {minutes}m",
            )
        return True, ""

    # ============ GANG SYSTEM ============

    async def create_gang(self, owner_id: int, name: str) -> int:
        """Create a new gang. Returns gang ID."""
        async with self.session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    insert(Gang)
                    .values(owner_id=owner_id, name=name)
                    .returning(Gang.id)
                )
                gang_id = result.scalar_one()
                await session.execute(
                    insert(GangMember).values(gang_id=gang_id, user_id=owner_id)
                )
                return gang_id

    async def get_gang(self, gang_id: int):
        """Get gang by ID."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(Gang).where(Gang.id == gang_id)
            )
            return _result_one(result)

    async def get_gang_by_name(self, name: str):
        """Get gang by name."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(Gang).where(Gang.name == name)
            )
            return _result_one(result)

    async def get_user_gang(self, user_id: int):
        """Get the gang a user belongs to."""
        async with self.session_factory() as session:
            result = await session.execute(
                text("""
                SELECT g.*, gm.joined_at, gm.last_left_at
                FROM gangs g
                JOIN gang_members gm ON g.id = gm.gang_id
                WHERE gm.user_id = :uid
                """),
                {"uid": user_id},
            )
            return _result_one(result)

    async def get_gang_members(self, gang_id: int):
        """Get all members of a gang (blocked users excluded)."""
        async with self.session_factory() as session:
            result = await session.execute(
                text("""
                SELECT u.user_id, u.first_name, u.username, gm.joined_at, gm.last_left_at
                FROM gang_members gm
                JOIN users u ON gm.user_id = u.user_id
                WHERE gm.gang_id = :gid
                  AND u.user_id NOT IN (SELECT user_id FROM blocked_users)
                ORDER BY gm.joined_at ASC
                """),
                {"gid": gang_id},
            )
            return _result_all(result)

    async def is_gang_owner(self, user_id: int, gang_id: int) -> bool:
        """Check if user is the owner of a gang."""
        async with self.session_factory() as session:
            return bool(
                await session.scalar(
                    select(
                        exists().where(
                            and_(Gang.id == gang_id, Gang.owner_id == user_id)
                        )
                    )
                )
            )

    async def join_gang(self, gang_id: int, user_id: int):
        """Add user to a gang."""
        async with self.session_factory() as session:
            await session.execute(
                insert(GangMember).values(gang_id=gang_id, user_id=user_id)
            )
            await session.commit()

    async def leave_gang(self, user_id: int):
        """Remove user from their gang and set last_left_at."""
        async with self.session_factory() as session:
            async with session.begin():
                await session.execute(
                    update(GangMember)
                    .where(GangMember.user_id == user_id)
                    .values(last_left_at=func.now())
                )
                await session.execute(
                    delete(GangMember).where(GangMember.user_id == user_id)
                )

    async def destroy_gang(self, gang_id: int):
        """Destroy a gang and remove all members.

        gang_members cascades automatically (ondelete=CASCADE), but
        gang_war_log and gang_immunity FKs do not — clear those rows
        first to avoid a foreign-key violation.
        """
        async with self.session_factory() as session:
            async with session.begin():
                await session.execute(
                    delete(GangWarLog).where(
                        or_(
                            GangWarLog.attacker_gang_id == gang_id,
                            GangWarLog.target_gang_id == gang_id,
                        )
                    )
                )
                await session.execute(
                    delete(GangImmunity).where(
                        or_(
                            GangImmunity.gang_id == gang_id,
                            GangImmunity.immune_from_gang_id == gang_id,
                        )
                    )
                )
                await session.execute(delete(Gang).where(Gang.id == gang_id))

    async def can_join_gang(self, user_id: int) -> bool:
        """Check if user can join a gang (not left today)."""
        from datetime import datetime, timezone

        current_gang = await self.get_user_gang(user_id)
        if current_gang:
            return False

        async with self.session_factory() as session:
            row = await session.execute(
                select(GangMember.last_left_at)
                .where(
                    GangMember.user_id == user_id,
                    GangMember.last_left_at.is_not(None),
                )
                .order_by(GangMember.last_left_at.desc())
                .limit(1)
            )
            last_left_row = row.fetchone()

        if not last_left_row:
            return True

        last_left = last_left_row[0]
        if last_left.tzinfo is not None:
            last_left = last_left.replace(tzinfo=None)

        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        today_utc_start = now_utc.replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        return last_left < today_utc_start

    async def add_gang_war(
        self,
        attacker_gang_id: int,
        target_gang_id: int,
        attacker_id: int,
        target_id: int,
        result: str,
        hearts_lost: int,
        reward_amount: int,
    ):
        """Log a gang war."""
        async with self.session_factory() as session:
            await session.execute(
                insert(GangWarLog).values(
                    attacker_gang_id=attacker_gang_id,
                    target_gang_id=target_gang_id,
                    attacker_id=attacker_id,
                    target_id=target_id,
                    result=result,
                    hearts_lost=hearts_lost,
                    reward_amount=reward_amount,
                )
            )
            await session.commit()

    async def get_gang_war_count(self, user_id: int) -> int:
        """Get number of gang wars user participated in today."""
        async with self.session_factory() as session:
            return (
                await session.scalar(
                    select(func.count(GangWarLog.id)).where(
                        or_(
                            GangWarLog.attacker_id == user_id,
                            GangWarLog.target_id == user_id,
                        ),
                        func.date(GangWarLog.created_at) == func.current_date(),
                    )
                )
                or 0
            )

    async def check_gang_immunity(
        self, gang_id: int, immune_from_gang_id: int
    ) -> bool:
        """Check if gang has immunity from another gang."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(GangImmunity).where(
                    GangImmunity.gang_id == gang_id,
                    GangImmunity.immune_from_gang_id == immune_from_gang_id,
                    GangImmunity.expires_at > func.now(),
                )
            )
            return _result_one(result) is not None

    async def get_gang_immunity_expiry(
        self, gang_id: int, immune_from_gang_id: int
    ):
        """Get active immunity expiry timestamp if present."""
        async with self.session_factory() as session:
            return await session.scalar(
                select(GangImmunity.expires_at).where(
                    GangImmunity.gang_id == gang_id,
                    GangImmunity.immune_from_gang_id == immune_from_gang_id,
                    GangImmunity.expires_at > func.now(),
                )
            )

    async def add_gang_immunity(
        self, gang_id: int, immune_from_gang_id: int, hours: int
    ):
        """Add gang immunity."""
        async with self.session_factory() as session:
            await session.execute(
                text("""
                INSERT INTO gang_immunity (gang_id, immune_from_gang_id, expires_at)
                VALUES (:gid, :igid, NOW() + INTERVAL '1 hour' * :hours)
                ON CONFLICT (gang_id, immune_from_gang_id)
                DO UPDATE SET expires_at = NOW() + INTERVAL '1 hour' * :hours
                """),
                {"gid": gang_id, "igid": immune_from_gang_id, "hours": hours},
            )
            await session.commit()

    async def had_successful_gang_war_today(
        self, attacker_gang_id: int, target_gang_id: int
    ) -> bool:
        """Check if attacker gang already won against target gang today."""
        async with self.session_factory() as session:
            return bool(
                await session.scalar(
                    text("""
                    SELECT EXISTS(
                        SELECT 1 FROM gang_war_log
                        WHERE attacker_gang_id = :attacker_gang_id
                          AND target_gang_id = :target_gang_id
                          AND result = 'win'
                          AND DATE(created_at) = CURRENT_DATE
                    )
                    """),
                    {
                        "attacker_gang_id": attacker_gang_id,
                        "target_gang_id": target_gang_id,
                    },
                )
            )

    async def get_gang_total_bank(self, gang_id: int) -> int:
        """Get total bank balance across gang members."""
        async with self.session_factory() as session:
            return (
                await session.scalar(
                    text("""
                    SELECT COALESCE(SUM(COALESCE(b.balance, 0)), 0)
                    FROM gang_members gm
                    LEFT JOIN bank_accounts b ON b.user_id = gm.user_id
                    WHERE gm.gang_id = :gang_id
                    """),
                    {"gang_id": gang_id},
                )
                or 0
            )

    async def take_from_gang_bank(self, gang_id: int, amount: int) -> int:
        """Deduct up to amount from gang members' bank accounts, oldest members first."""
        if amount <= 0:
            return 0

        deducted_total = 0
        async with self.session_factory() as session:
            async with session.begin():
                rows = await session.execute(
                    text("""
                    SELECT gm.user_id, COALESCE(b.balance, 0) AS balance
                    FROM gang_members gm
                    LEFT JOIN bank_accounts b ON b.user_id = gm.user_id
                    WHERE gm.gang_id = :gang_id
                    ORDER BY gm.joined_at ASC
                    """),
                    {"gang_id": gang_id},
                )
                members = rows.fetchall()

                remaining = amount
                for member in members:
                    user_id = member[0]
                    balance = int(member[1] or 0)
                    if balance <= 0 or remaining <= 0:
                        continue

                    take = min(balance, remaining)
                    await session.execute(
                        text("""
                        UPDATE bank_accounts
                        SET balance = balance - :take, last_updated = NOW()
                        WHERE user_id = :user_id
                        """),
                        {"take": take, "user_id": user_id},
                    )
                    await session.execute(
                        text(
                            "INSERT INTO transactions (user_id, amount, reason) VALUES (:uid, :amount, :reason)"
                        ),
                        {
                            "uid": user_id,
                            "amount": -take,
                            "reason": "Gang war loss",
                        },
                    )
                    deducted_total += take
                    remaining -= take

        return deducted_total

    # ============ SECURITY SYSTEM ============

    async def get_user_security(self, user_id: int):
        """Get user's security status."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(UserSecurity).where(UserSecurity.user_id == user_id)
            )
            return _result_one(result)

    async def buy_security(self, user_id: int, cost: int):
        """Buy security system for user."""
        async with self.session_factory() as session:
            stmt = pg_insert(UserSecurity).values(
                user_id=user_id, is_active=True, purchased_at=func.now()
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["user_id"],
                set_={
                    "is_active": True,
                    "purchased_at": func.now(),
                    "broken_at": None,
                },
            )
            await session.execute(stmt)
            await session.commit()
        # Deduct cost
        await self.add_balance(user_id, -cost, "Security system purchase")

    async def break_security(self, user_id: int):
        """Break user's security system."""
        async with self.session_factory() as session:
            await session.execute(
                update(UserSecurity)
                .where(UserSecurity.user_id == user_id)
                .values(is_active=False, broken_at=func.now())
            )
            await session.commit()

    # ============ HEIST SYSTEM ============

    async def log_heist(
        self,
        user_id: int,
        target_id: int,
        result: str,
        amount: int,
    ):
        """Log a heist attempt."""
        async with self.session_factory() as session:
            await session.execute(
                insert(HeistLog).values(
                    user_id=user_id,
                    target_id=target_id,
                    result=result,
                    amount=amount,
                )
            )
            await session.commit()

    async def get_heist_count_today(self, user_id: int) -> int:
        """Get number of heists user performed today."""
        async with self.session_factory() as session:
            return (
                await session.scalar(
                    select(func.count(HeistLog.id)).where(
                        HeistLog.user_id == user_id,
                        func.date(HeistLog.created_at) == func.current_date(),
                    )
                )
                or 0
            )

    async def can_heist_target(
        self, attacker_id: int, target_id: int, hours: int = 6
    ) -> bool:
        """Check if attacker can heist target (cooldown check)."""
        async with self.session_factory() as session:
            result = await session.execute(
                text("""
                SELECT * FROM heist_log
                WHERE user_id = :uid AND target_id = :tid
                AND created_at > NOW() - INTERVAL '1 hour' * :hours
                ORDER BY created_at DESC
                LIMIT 1
                """),
                {"uid": attacker_id, "tid": target_id, "hours": hours},
            )
            return _result_one(result) is None

    async def get_bank_balance(self, user_id: int):
        """Get user's bank account balance."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(BankAccount).where(BankAccount.user_id == user_id)
            )
            return _result_one(result)

    # ============ HEIST VAULT STORAGE ============

    async def store_heist_vault(
        self,
        attacker_id: int,
        target_id: int,
        correct_vault: int,
        num_vaults: int,
    ):
        """Store the correct vault for a pending heist."""
        async with self.session_factory() as session:
            stmt = pg_insert(HeistVault).values(
                attacker_id=attacker_id,
                target_id=target_id,
                correct_vault=correct_vault,
                num_vaults=num_vaults,
            )
            excluded = stmt.excluded
            stmt = stmt.on_conflict_do_update(
                index_elements=["attacker_id", "target_id"],
                set_={
                    "correct_vault": excluded.correct_vault,
                    "num_vaults": excluded.num_vaults,
                    "created_at": func.now(),
                },
            )
            await session.execute(stmt)
            await session.commit()

    async def get_heist_vault(self, attacker_id: int, target_id: int):
        """Get stored heist vault info."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(HeistVault).where(
                    HeistVault.attacker_id == attacker_id,
                    HeistVault.target_id == target_id,
                )
            )
            return _result_one(result)

    async def delete_heist_vault(self, attacker_id: int, target_id: int):
        """Delete stored heist vault after heist is complete."""
        async with self.session_factory() as session:
            await session.execute(
                delete(HeistVault).where(
                    HeistVault.attacker_id == attacker_id,
                    HeistVault.target_id == target_id,
                )
            )
            await session.commit()

    # ============ 4 PIC GAME ============

    async def create_four_pic_game(
        self,
        chat_id: int,
        word: str,
        category: str,
        hint_message: str,
        photo_b64: str,
    ):
        """Create a new 4-pic game."""
        async with self.session_factory() as session:
            stmt = pg_insert(FourPicGame).values(
                chat_id=chat_id,
                word=word,
                category=category,
                hint_message=hint_message,
                photo_b64=photo_b64,
            )
            excluded = stmt.excluded
            stmt = stmt.on_conflict_do_update(
                index_elements=["chat_id"],
                set_={
                    "word": excluded.word,
                    "category": excluded.category,
                    "hint_message": excluded.hint_message,
                    "photo_b64": excluded.photo_b64,
                    "is_category_hint_given": False,
                    "is_hint_message_given": False,
                    "revealed_letters": text("'[]'::jsonb"),
                    "created_at": func.now(),
                },
            )
            await session.execute(stmt)
            await session.commit()

    async def get_four_pic_game(self, chat_id: int):
        """Get active 4-pic game for a chat."""
        async with self.session_factory() as session:
            result = await session.execute(
                select(FourPicGame).where(FourPicGame.chat_id == chat_id)
            )
            return _result_one(result)

    async def delete_four_pic_game(self, chat_id: int):
        """Delete 4-pic game when it ends."""
        async with self.session_factory() as session:
            await session.execute(
                delete(FourPicGame).where(FourPicGame.chat_id == chat_id)
            )
            await session.commit()

    async def update_four_pic_hint_category(self, chat_id: int):
        """Mark category hint as given."""
        async with self.session_factory() as session:
            await session.execute(
                update(FourPicGame)
                .where(FourPicGame.chat_id == chat_id)
                .values(is_category_hint_given=True)
            )
            await session.commit()

    async def update_four_pic_hint_message(self, chat_id: int):
        """Mark hint message as given."""
        async with self.session_factory() as session:
            await session.execute(
                update(FourPicGame)
                .where(FourPicGame.chat_id == chat_id)
                .values(is_hint_message_given=True)
            )
            await session.commit()

    async def add_four_pic_revealed_letter(
        self, chat_id: int, letter_index: int
    ):
        """Add a revealed letter index to the game."""
        current = await self.get_four_pic_revealed_letters(chat_id)

        if letter_index not in current:
            current.append(letter_index)
            current.sort()

        async with self.session_factory() as session:
            await session.execute(
                update(FourPicGame)
                .where(FourPicGame.chat_id == chat_id)
                .values(revealed_letters=current)
            )
            await session.commit()

    async def get_four_pic_revealed_letters(self, chat_id: int) -> list:
        """Get list of revealed letter indices."""
        async with self.session_factory() as session:
            result = await session.scalar(
                select(FourPicGame.revealed_letters).where(
                    FourPicGame.chat_id == chat_id
                )
            )
        if result is None:
            return []
        if isinstance(result, list):
            return result
        try:
            return json.loads(result)
        except Exception:
            return []

    async def transfer_account(
        self,
        from_user_id: int,
        to_user_id: int,
        transfer_money: bool = True,
        transfer_family: bool = True,
        transfer_friends: bool = True,
        transfer_inventory: bool = True,
        transfer_other: bool = True,
    ):
        """Transfer selected categories of data from from_user_id to to_user_id.

        Merge categories (money, friends, inventory): destination keeps its
        existing rows; source rows are summed/deduped into destination.

        Replace categories (family, other): destination's rows are purged and
        source rows are repointed onto destination.

        When every category is selected, the source User row stays but has
        `transferred = to_user_id` set so the funeral system and history
        lookups can still find it. With partial selection the source remains
        a live, independent account.
        """
        if from_user_id == to_user_id:
            raise ValueError(
                "Source and destination user IDs must be different"
            )

        FAMILY_TABLES = {
            "family_relationships",
            "marriages",
            "siblings",
            "pending_requests",
        }
        FRIENDS_TABLES = {
            "friendships",
            "friend_requests",
            "friend_ratings",
            "friend_links",
        }
        INVENTORY_TABLES = {"inventory"}
        # Money handled via dedicated merge path below.
        MONEY_TABLES = {"wallets", "bank_accounts", "transactions"}
        CONSTRAINED_MODELS = {
            "friendships": Friendship,
            "marriages": Marriage,
            "siblings": Sibling,
        }

        user_fk_columns = []
        for table in Base.metadata.sorted_tables:
            if table.name == User.__tablename__:
                continue

            fk_columns = [
                column
                for column in table.columns
                if any(
                    fk.column.table.name == User.__tablename__
                    and fk.column.name == "user_id"
                    for fk in column.foreign_keys
                )
            ]
            if fk_columns:
                user_fk_columns.append((table, fk_columns))

        known_named = (
            FAMILY_TABLES | FRIENDS_TABLES | MONEY_TABLES | INVENTORY_TABLES
        )
        other_tables = {
            table.name
            for table, _ in user_fk_columns
            if table.name not in known_named
        }

        # Tables we'll touch in the generic repoint loop (step 4). Friends
        # are in here for dedupe+repoint; inventory has its own dedicated
        # merge path further down.
        selected_tables: set[str] = set()
        if transfer_family:
            selected_tables |= FAMILY_TABLES
        if transfer_friends:
            selected_tables |= FRIENDS_TABLES
        if transfer_money:
            # Transactions go through the generic repoint loop; wallets and
            # bank_accounts are handled by the dedicated merge path.
            selected_tables.add("transactions")
        if transfer_other:
            selected_tables |= other_tables

        # Tables that get a destination-purge before repointing (replace mode).
        purge_tables: set[str] = set()
        if transfer_family:
            purge_tables |= FAMILY_TABLES
        if transfer_other:
            purge_tables |= other_tables
        # Note: friends and inventory are MERGE — no destination purge.

        all_yes = (
            transfer_money
            and transfer_family
            and transfer_friends
            and transfer_inventory
            and transfer_other
        )

        async with self.session_factory() as session:
            async with session.begin():
                from_user = await session.scalar(
                    select(User).where(User.user_id == from_user_id)
                )
                if from_user is None:
                    raise ValueError(
                        f"Source user {from_user_id} does not exist"
                    )
                to_user = await session.scalar(
                    select(User).where(User.user_id == to_user_id)
                )

                # 1) Purge destination-user rows ONLY in replace categories.
                # Merge categories (friends, inventory) keep destination rows.
                for table, fk_columns in user_fk_columns:
                    if table.name not in purge_tables:
                        continue
                    await session.execute(
                        delete(table).where(
                            or_(*[
                                column == to_user_id for column in fk_columns
                            ])
                        )
                    )

                # 2) Ensure the destination User row exists / is correct.
                # Money merge keeps user2's wallet rows alive, so we can't
                # DELETE user2's User row here — overwrite with UPDATE instead.
                if all_yes:
                    if to_user is None:
                        await session.execute(
                            insert(User).values(
                                user_id=to_user_id,
                                username=from_user.username,
                                first_name=from_user.first_name,
                                profile_pic_file_id=from_user.profile_pic_file_id,
                                profile_pic_b64=from_user.profile_pic_b64,
                                hearts=from_user.hearts,
                                last_updated=from_user.last_updated,
                            )
                        )
                    else:
                        await session.execute(
                            update(User)
                            .where(User.user_id == to_user_id)
                            .values(
                                username=from_user.username,
                                first_name=from_user.first_name,
                                profile_pic_file_id=from_user.profile_pic_file_id,
                                profile_pic_b64=from_user.profile_pic_b64,
                                hearts=from_user.hearts,
                                last_updated=from_user.last_updated,
                            )
                        )
                elif to_user is None:
                    await session.execute(
                        insert(User).values(
                            user_id=to_user_id,
                            username=from_user.username,
                            first_name=from_user.first_name,
                            profile_pic_file_id=from_user.profile_pic_file_id,
                            profile_pic_b64=from_user.profile_pic_b64,
                            hearts=from_user.hearts,
                            last_updated=from_user.last_updated,
                        )
                    )

                # 3) Resolve constraint conflicts before repointing for
                # friendship/marriage/sibling (user1_id < user2_id + unique pair).
                for table_name, model_class in CONSTRAINED_MODELS.items():
                    if table_name not in selected_tables:
                        continue

                    from_relationships = await session.scalars(
                        select(model_class).where(
                            or_(
                                model_class.user1_id == from_user_id,
                                model_class.user2_id == from_user_id,
                            )
                        )
                    )

                    to_delete_ids = []
                    for relationship in from_relationships:
                        other_user = (
                            relationship.user2_id
                            if relationship.user1_id == from_user_id
                            else relationship.user1_id
                        )
                        if other_user == to_user_id:
                            # Self-relationship after repoint — drop it.
                            to_delete_ids.append(relationship.id)
                            continue

                        smaller = min(to_user_id, other_user)
                        larger = max(to_user_id, other_user)

                        duplicate = await session.scalar(
                            select(model_class).where(
                                and_(
                                    model_class.user1_id == smaller,
                                    model_class.user2_id == larger,
                                )
                            )
                        )
                        if duplicate:
                            to_delete_ids.append(relationship.id)

                    if to_delete_ids:
                        await session.execute(
                            delete(model_class).where(
                                model_class.id.in_(to_delete_ids)
                            )
                        )

                # 4) Repoint source rows to destination in selected categories.
                for table, fk_columns in user_fk_columns:
                    if table.name not in selected_tables:
                        continue

                    if table.name in CONSTRAINED_MODELS:
                        model_class = CONSTRAINED_MODELS[table.name]
                        relationships = await session.scalars(
                            select(model_class).where(
                                or_(
                                    model_class.user1_id == from_user_id,
                                    model_class.user2_id == from_user_id,
                                )
                            )
                        )
                        for relationship in relationships:
                            other_user = (
                                relationship.user2_id
                                if relationship.user1_id == from_user_id
                                else relationship.user1_id
                            )
                            relationship.user1_id = min(to_user_id, other_user)
                            relationship.user2_id = max(to_user_id, other_user)
                    else:
                        for column in fk_columns:
                            await session.execute(
                                update(table)
                                .where(column == from_user_id)
                                .values({column.name: to_user_id})
                            )

                # 5) Money merge — sum source balances onto destination, then
                # remove source money rows. Wallet/BankAccount have user_id as
                # primary key so a plain repoint would collide.
                if transfer_money:
                    for model_class in (Wallet, BankAccount):
                        from_money = await session.scalar(
                            select(model_class).where(
                                model_class.user_id == from_user_id
                            )
                        )
                        if from_money is None:
                            continue

                        to_money = await session.scalar(
                            select(model_class).where(
                                model_class.user_id == to_user_id
                            )
                        )
                        if to_money is None:
                            values = {
                                "user_id": to_user_id,
                                "balance": from_money.balance or 0,
                            }
                            if model_class is Wallet:
                                values["total_earned"] = (
                                    from_money.total_earned or 0
                                )
                            await session.execute(
                                insert(model_class).values(**values)
                            )
                        else:
                            to_money.balance = (to_money.balance or 0) + (
                                from_money.balance or 0
                            )
                            if model_class is Wallet:
                                to_money.total_earned = (
                                    to_money.total_earned or 0
                                ) + (from_money.total_earned or 0)

                        await session.execute(
                            delete(model_class).where(
                                model_class.user_id == from_user_id
                            )
                        )

                # 5b) Inventory merge — sum quantities on (item_type, item_name)
                # collisions; otherwise repoint the source row's user_id.
                if transfer_inventory:
                    from_items = await session.scalars(
                        select(Inventory).where(
                            Inventory.user_id == from_user_id
                        )
                    )
                    for from_item in from_items:
                        existing = await session.scalar(
                            select(Inventory).where(
                                Inventory.user_id == to_user_id,
                                Inventory.item_type == from_item.item_type,
                                Inventory.item_name == from_item.item_name,
                            )
                        )
                        if existing is None:
                            from_item.user_id = to_user_id
                        else:
                            existing.quantity = (existing.quantity or 0) + (
                                from_item.quantity or 0
                            )
                            await session.delete(from_item)

                await session.flush()

                # 6) Keep the source User row when every category moved, but
                # mark it as transferred so funerals / historical lookups can
                # follow the move.
                if all_yes:
                    await session.execute(
                        update(User)
                        .where(User.user_id == from_user_id)
                        .values(transferred=to_user_id)
                    )

    # ========== Blocked Users ==========

    async def is_blocked(self, user_id: int) -> Optional[str]:
        """Return None if not blocked, else the ban reason (may be empty string)."""
        async with self.session_factory() as session:
            result = await session.scalar(
                select(BlockedUser).where(BlockedUser.user_id == user_id)
            )
            if result is None:
                return None
            return result.reason or ""

    async def block_user(
        self, user_id: int, reason: Optional[str] = None
    ) -> bool:
        """Return True if newly blocked, False if already blocked."""
        async with self.session_factory() as session:
            async with session.begin():
                existing = await session.scalar(
                    select(BlockedUser).where(BlockedUser.user_id == user_id)
                )
                if existing is not None:
                    return False
                await session.execute(
                    insert(BlockedUser).values(user_id=user_id, reason=reason)
                )
                return True

    async def unblock_user(self, user_id: int) -> bool:
        """Return True if removed, False if user wasn't blocked."""
        async with self.session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    delete(BlockedUser).where(BlockedUser.user_id == user_id)
                )
                return (result.rowcount or 0) > 0
