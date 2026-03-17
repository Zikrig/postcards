"""Database repository: users, prompts, promo codes, state."""
import json
import logging
from typing import Any, Optional

import asyncpg

logger = logging.getLogger(__name__)


class Repo:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    async def init(self) -> None:
        logger.info("Initializing Repo schema...")
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    tg_id BIGINT UNIQUE NOT NULL,
                    username TEXT,
                    full_name TEXT,
                    is_authorized BOOLEAN NOT NULL DEFAULT FALSE,
                    is_admin BOOLEAN NOT NULL DEFAULT FALSE,
                    balance_tokens INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """
            )
            await conn.execute(
                """
                ALTER TABLE users
                ADD COLUMN IF NOT EXISTS balance_tokens INTEGER NOT NULL DEFAULT 0;
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS prompts (
                    id SERIAL PRIMARY KEY,
                    title TEXT UNIQUE NOT NULL,
                    template TEXT NOT NULL,
                    variable_descriptions JSONB NOT NULL DEFAULT '{}'::jsonb,
                    reference_photo_file_id TEXT,
                    created_by BIGINT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """
            )
            await conn.execute(
                """
                ALTER TABLE prompts
                ADD COLUMN IF NOT EXISTS variable_descriptions JSONB NOT NULL DEFAULT '{}'::jsonb;
                """
            )
            await conn.execute(
                """
                ALTER TABLE prompts
                ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;
                """
            )
            await conn.execute(
                """
                ALTER TABLE prompts
                ADD COLUMN IF NOT EXISTS feach_data JSONB;
                """
            )
            await conn.execute(
                """
                ALTER TABLE prompts
                ADD COLUMN IF NOT EXISTS example_file_ids JSONB NOT NULL DEFAULT '[]'::jsonb;
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS bot_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS promo_codes (
                    id SERIAL PRIMARY KEY,
                    code TEXT UNIQUE NOT NULL,
                    credits_amount INTEGER NOT NULL,
                    max_uses INTEGER,
                    uses_count INTEGER NOT NULL DEFAULT 0,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    created_by BIGINT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS promo_redemptions (
                    id SERIAL PRIMARY KEY,
                    promo_id INTEGER NOT NULL REFERENCES promo_codes(id) ON DELETE CASCADE,
                    user_tg_id BIGINT NOT NULL,
                    redeemed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE (promo_id, user_tg_id)
                );
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tags (
                    id SERIAL PRIMARY KEY,
                    name TEXT UNIQUE NOT NULL
                );
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS prompt_tags (
                    prompt_id INTEGER NOT NULL REFERENCES prompts (id) ON DELETE CASCADE,
                    tag_id INTEGER NOT NULL REFERENCES tags (id) ON DELETE CASCADE,
                    PRIMARY KEY (prompt_id, tag_id)
                );
                """
            )
            await conn.execute(
                """
                ALTER TABLE prompts
                ADD COLUMN IF NOT EXISTS owner_tg_id BIGINT,
                ADD COLUMN IF NOT EXISTS is_public BOOLEAN NOT NULL DEFAULT FALSE,
                ADD COLUMN IF NOT EXISTS source_prompt_id INTEGER;
                """
            )
            await conn.execute(
                """
                ALTER TABLE prompts
                ADD COLUMN IF NOT EXISTS description TEXT;
                """
            )
            await conn.execute(
                """
                UPDATE prompts SET description = title WHERE description IS NULL OR description = '';
                """
            )
            # Create system tags
            for tag_name in ["Main Menu", "Users"]:
                tag = await conn.fetchrow("SELECT id FROM tags WHERE name = $1", tag_name)
                if not tag:
                    await conn.execute("INSERT INTO tags (name) VALUES ($1)", tag_name)

    async def upsert_user(
        self,
        tg_id: int,
        username: str,
        full_name: str,
        is_admin: bool,
    ) -> asyncpg.Record:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO users (tg_id, username, full_name, is_authorized, is_admin)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (tg_id) DO UPDATE
                SET username = EXCLUDED.username,
                    full_name = EXCLUDED.full_name,
                    is_admin = users.is_admin OR EXCLUDED.is_admin,
                    is_authorized = CASE WHEN EXCLUDED.is_admin THEN TRUE ELSE users.is_authorized END
                RETURNING *;
                """,
                tg_id,
                username,
                full_name,
                is_admin,
                is_admin,
            )
            return row

    async def get_user(self, tg_id: int) -> Optional[asyncpg.Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetchrow("SELECT * FROM users WHERE tg_id = $1", tg_id)

    async def get_user_by_username(self, username: str) -> Optional[asyncpg.Record]:
        name = (username or "").strip().lstrip("@")
        if not name:
            return None
        async with self.pool.acquire() as conn:
            return await conn.fetchrow("SELECT * FROM users WHERE username = $1", name)

    async def set_user_authorized(self, tg_id: int, value: bool) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET is_authorized = $1 WHERE tg_id = $2",
                value,
                tg_id,
            )

    async def get_user_balance(self, tg_id: int) -> int:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT balance_tokens FROM users WHERE tg_id = $1", tg_id)
            if not row:
                return 0
            return int(row["balance_tokens"] or 0)

    async def add_user_balance(self, tg_id: int, amount: int) -> int:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE users
                SET balance_tokens = balance_tokens + $1
                WHERE tg_id = $2
                RETURNING balance_tokens
                """,
                amount,
                tg_id,
            )
            if not row:
                return 0
            return int(row["balance_tokens"] or 0)

    async def consume_generation_token(self, tg_id: int) -> Optional[int]:
        return await self.consume_tokens(tg_id, 1)

    async def consume_tokens(self, tg_id: int, amount: int) -> Optional[int]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE users
                SET balance_tokens = balance_tokens - $1
                WHERE tg_id = $2 AND balance_tokens >= $1
                RETURNING balance_tokens
                """,
                amount,
                tg_id,
            )
            if not row:
                return None
            return int(row["balance_tokens"] or 0)

    PAGE_SIZE = 20

    async def list_prompts(self, active_only: bool = False) -> list[asyncpg.Record]:
        async with self.pool.acquire() as conn:
            where_clauses = ["owner_tg_id IS NULL"]
            if active_only:
                where_clauses.append("is_active = TRUE")
            where = " WHERE " + " AND ".join(where_clauses)
            return await conn.fetch(f"SELECT * FROM prompts {where} ORDER BY id DESC")

    async def list_prompts_paginated(
        self, active_only: bool = False, page: int = 0, per_page: int = 20
    ) -> tuple[list[asyncpg.Record], int]:
        async with self.pool.acquire() as conn:
            where_clauses = ["owner_tg_id IS NULL"]
            if active_only:
                where_clauses.append("is_active = TRUE")
            where = " WHERE " + " AND ".join(where_clauses)
            total = await conn.fetchval(f"SELECT COUNT(*) FROM prompts {where}")
            total = int(total or 0)
            offset = max(0, page) * per_page
            rows = await conn.fetch(
                f"SELECT * FROM prompts {where} ORDER BY id DESC LIMIT $1 OFFSET $2",
                per_page,
                offset,
            )
            return list(rows), total

    async def list_user_prompts_paginated(
        self, owner_tg_id: int, page: int = 0, per_page: int = 20
    ) -> tuple[list[asyncpg.Record], int]:
        async with self.pool.acquire() as conn:
            where = "WHERE owner_tg_id = $1"
            total = await conn.fetchval(f"SELECT COUNT(*) FROM prompts {where}", owner_tg_id)
            total = int(total or 0)
            offset = max(0, page) * per_page
            rows = await conn.fetch(
                f"SELECT * FROM prompts {where} ORDER BY id DESC LIMIT $2 OFFSET $3",
                owner_tg_id,
                per_page,
                offset,
            )
            return list(rows), total

    async def list_public_user_prompts_paginated(
        self, tag_id: Optional[int] = None, page: int = 0, per_page: int = 20
    ) -> tuple[list[asyncpg.Record], int]:
        """List all public user prompts, optionally filtered by tag."""
        async with self.pool.acquire() as conn:
            where_clauses = ["p.owner_tg_id IS NOT NULL", "p.is_public = TRUE", "p.is_active = TRUE"]
            args = []
            if tag_id and tag_id > 0:
                where_clauses.append("pt.tag_id = $1")
                args.append(tag_id)
            
            where = " WHERE " + " AND ".join(where_clauses)
            
            from_sql = "FROM prompts p"
            if tag_id and tag_id > 0:
                from_sql += " INNER JOIN prompt_tags pt ON pt.prompt_id = p.id"
            
            total = await conn.fetchval(f"SELECT COUNT(*) {from_sql} {where}", *args)
            total = int(total or 0)
            
            offset = max(0, page) * per_page
            sql = f"""
                SELECT p.* {from_sql}
                {where}
                ORDER BY p.id DESC
                LIMIT ${len(args)+1} OFFSET ${len(args)+2}
            """
            rows = await conn.fetch(sql, *args, per_page, offset)
            return list(rows), total

    async def list_users_with_prompts_paginated(
        self, page: int = 0, per_page: int = 20
    ) -> tuple[list[asyncpg.Record], int]:
        async with self.pool.acquire() as conn:
            sql_count = "SELECT COUNT(DISTINCT owner_tg_id) FROM prompts WHERE owner_tg_id IS NOT NULL"
            total = await conn.fetchval(sql_count)
            total = int(total or 0)
            offset = max(0, page) * per_page
            sql = """
                SELECT DISTINCT u.tg_id, u.username, u.full_name
                FROM users u
                INNER JOIN prompts p ON p.owner_tg_id = u.tg_id
                ORDER BY u.tg_id
                LIMIT $1 OFFSET $2
            """
            rows = await conn.fetch(sql, per_page, offset)
            return list(rows), total

    async def get_prompt_by_title(self, title: str) -> Optional[asyncpg.Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetchrow("SELECT * FROM prompts WHERE title = $1", title)

    async def get_prompt_by_id(self, prompt_id: int) -> Optional[asyncpg.Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetchrow("SELECT * FROM prompts WHERE id = $1", prompt_id)

    async def insert_prompt(
        self,
        title: str,
        template: str,
        variable_descriptions: dict[str, Any],
        reference_photo_file_id: Optional[str],
        created_by: int,
        is_active: bool = False,
        feach_data: Optional[dict[str, Any]] = None,
        owner_tg_id: Optional[int] = None,
        is_public: bool = False,
        description: Optional[str] = None,
    ) -> int:
        desc = (description or title).strip() or title
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO prompts (title, template, variable_descriptions, reference_photo_file_id, created_by, is_active, feach_data, owner_tg_id, is_public, description)
                VALUES ($1, $2, $3::jsonb, $4, $5, $6, $7::jsonb, $8, $9, $10)
                RETURNING id
                """,
                title,
                template,
                json.dumps(variable_descriptions),
                reference_photo_file_id,
                created_by,
                is_active,
                json.dumps(feach_data) if feach_data is not None else None,
                owner_tg_id,
                is_public,
                desc,
            )
            return int(row["id"])

    async def update_prompt_public(self, prompt_id: int, is_public: bool) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE prompts
                SET is_public = $1,
                    -- Если делаем промпт публичным, автоматически включаем его
                    is_active = CASE WHEN $1 THEN TRUE ELSE is_active END
                WHERE id = $2
                """,
                is_public,
                prompt_id,
            )

    async def update_prompt(
        self,
        prompt_id: int,
        title: str,
        template: str,
        variable_descriptions: dict[str, Any],
        reference_photo_file_id: Optional[str],
    ) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE prompts
                SET title = $1,
                    template = $2,
                    variable_descriptions = $3::jsonb,
                    reference_photo_file_id = $4
                WHERE id = $5
                """,
                title,
                template,
                json.dumps(variable_descriptions),
                reference_photo_file_id,
                prompt_id,
            )

    async def set_prompt_active(self, prompt_id: int, is_active: bool) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE prompts SET is_active = $1 WHERE id = $2",
                is_active,
                prompt_id,
            )

    async def update_prompt_feach_data(
        self, prompt_id: int, feach_data: Optional[dict[str, Any]]
    ) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE prompts SET feach_data = $1::jsonb WHERE id = $2",
                json.dumps(feach_data) if feach_data is not None else None,
                prompt_id,
            )

    async def update_prompt_description(self, prompt_id: int, description: str) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE prompts SET description = $1 WHERE id = $2",
                (description or "").strip() or "",
                prompt_id,
            )

    async def set_prompt_examples(self, prompt_id: int, example_file_ids: list[str]) -> None:
        if len(example_file_ids) > 3:
            example_file_ids = example_file_ids[:3]
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE prompts SET example_file_ids = $1::jsonb WHERE id = $2",
                json.dumps(example_file_ids),
                prompt_id,
            )

    async def clone_prompt(self, source_id: int, target_title: str) -> int:
        async with self.pool.acquire() as conn:
            source = await conn.fetchrow("SELECT * FROM prompts WHERE id = $1", source_id)
            if not source:
                raise ValueError("Source prompt not found")
            
            desc = source.get("description") or source.get("title") or target_title
            # Log source prompt important fields for debugging cloning behaviour
            logger.info(
                "clone_prompt: source_id=%s title=%r ref_id=%r examples=%r is_public=%r is_active=%r",
                source.get("id"),
                source.get("title"),
                source.get("reference_photo_file_id"),
                source.get("example_file_ids"),
                source.get("is_public"),
                source.get("is_active"),
            )
            # Normalize examples to JSON string
            examples_raw = source.get("example_file_ids")
            if isinstance(examples_raw, str):
                examples_json = examples_raw or "[]"
            else:
                examples_json = json.dumps(examples_raw or [])

            # Try to insert with unique title; if conflict, add numeric suffixes
            base_title = target_title
            last_exc: Exception | None = None
            for i in range(1, 6):
                if i == 1:
                    title_try = base_title
                else:
                    title_try = f"{base_title} ({i})"
                try:
                    row = await conn.fetchrow(
                        """
                        INSERT INTO prompts (
                            title,
                            template,
                            variable_descriptions,
                            reference_photo_file_id,
                            created_by,
                            is_active,
                            feach_data,
                            owner_tg_id,
                            is_public,
                            source_prompt_id,
                            description,
                            example_file_ids
                        )
                        VALUES (
                            $1,
                            $2,
                            $3::jsonb,
                            $4,
                            $5,
                            TRUE,
                            $6::jsonb,
                            NULL,
                            TRUE,
                            $7,
                            $8,
                            $9::jsonb
                        )
                        RETURNING id
                        """,
                        title_try,
                        source["template"],
                        source["variable_descriptions"],
                        source["reference_photo_file_id"],
                        source["created_by"],
                        source["feach_data"],
                        source_id,
                        desc,
                        examples_json,
                    )
                    logger.info("clone_prompt: created clone id=%s with title=%r", row["id"], title_try)
                    return int(row["id"])
                except asyncpg.UniqueViolationError as e:  # type: ignore[attr-defined]
                    # Title already exists, try next suffix
                    logger.info("clone_prompt: title %r already exists, trying another suffix", title_try)
                    last_exc = e
                    continue
            # If we are here, all attempts failed
            if last_exc:
                raise last_exc
            raise RuntimeError("clone_prompt: failed to insert clone for unknown reason")

    async def list_tags(self) -> list[asyncpg.Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetch("SELECT * FROM tags ORDER BY name")

    async def list_tags_paginated(
        self, page: int = 0, per_page: int = 20
    ) -> tuple[list[asyncpg.Record], int]:
        async with self.pool.acquire() as conn:
            total = await conn.fetchval("SELECT COUNT(*) FROM tags")
            total = int(total or 0)
            offset = max(0, page) * per_page
            rows = await conn.fetch(
                "SELECT * FROM tags ORDER BY name LIMIT $1 OFFSET $2",
                per_page,
                offset,
            )
            return list(rows), total

    async def list_community_tags_paginated(
        self, page: int = 0, per_page: int = 20
    ) -> tuple[list[asyncpg.Record], int]:
        """
        Tags actually used by public user prompts (community prompts).
        Excludes tags that are never attached to any active public user prompt.
        """
        async with self.pool.acquire() as conn:
            base_where = """
                FROM tags t
                INNER JOIN prompt_tags pt ON pt.tag_id = t.id
                INNER JOIN prompts p ON p.id = pt.prompt_id
                WHERE p.owner_tg_id IS NOT NULL
                  AND p.is_public = TRUE
                  AND p.is_active = TRUE
            """
            total_sql = f"SELECT COUNT(DISTINCT t.id) {base_where}"
            total = await conn.fetchval(total_sql)
            total = int(total or 0)

            offset = max(0, page) * per_page
            rows_sql = f"""
                SELECT DISTINCT t.*
                {base_where}
                ORDER BY t.name
                LIMIT $1 OFFSET $2
            """
            rows = await conn.fetch(rows_sql, per_page, offset)
            return list(rows), total

    async def get_tag_by_id(self, tag_id: int) -> Optional[asyncpg.Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetchrow("SELECT * FROM tags WHERE id = $1", tag_id)

    async def get_tag_by_name(self, name: str) -> Optional[asyncpg.Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetchrow("SELECT * FROM tags WHERE name = $1", name.strip())

    async def create_tag(self, name: str) -> asyncpg.Record:
        name = name.strip()
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO tags (name) VALUES ($1) ON CONFLICT (name) DO UPDATE SET name = $1 RETURNING *",
                name,
            )
            return row

    async def update_tag(self, tag_id: int, name: str) -> None:
        name = name.strip()
        async with self.pool.acquire() as conn:
            await conn.execute("UPDATE tags SET name = $1 WHERE id = $2", name, tag_id)

    async def delete_tag(self, tag_id: int) -> bool:
        async with self.pool.acquire() as conn:
            result = await conn.execute("DELETE FROM tags WHERE id = $1", tag_id)
            return result.endswith("1")

    async def get_prompt_tag_ids(self, prompt_id: int) -> list[int]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT tag_id FROM prompt_tags WHERE prompt_id = $1 ORDER BY tag_id",
                prompt_id,
            )
            return [int(r["tag_id"]) for r in rows]

    async def set_prompt_tags(self, prompt_id: int, tag_ids: list[int]) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute("DELETE FROM prompt_tags WHERE prompt_id = $1", prompt_id)
            for tag_id in tag_ids:
                await conn.execute(
                    "INSERT INTO prompt_tags (prompt_id, tag_id) VALUES ($1, $2)",
                    prompt_id,
                    tag_id,
                )

    async def list_prompts_with_tag(self, tag_id: int, active_only: bool = True) -> list[asyncpg.Record]:
        async with self.pool.acquire() as conn:
            tag = await conn.fetchrow("SELECT name FROM tags WHERE id = $1", tag_id)
            is_users_tag = tag and tag["name"] == "Users"
            
            where_clauses = ["pt.tag_id = $1"]
            if not is_users_tag:
                where_clauses.append("p.owner_tg_id IS NULL")
            if active_only:
                where_clauses.append("p.is_active = TRUE")
            
            where = " WHERE " + " AND ".join(where_clauses)
            sql = f"""
                SELECT p.* FROM prompts p
                INNER JOIN prompt_tags pt ON pt.prompt_id = p.id
                {where}
                ORDER BY p.id DESC
            """
            return await conn.fetch(sql, tag_id)

    async def list_prompts_with_tag_paginated(
        self, tag_id: int, active_only: bool = True, page: int = 0, per_page: int = 20
    ) -> tuple[list[asyncpg.Record], int]:
        async with self.pool.acquire() as conn:
            tag = await conn.fetchrow("SELECT name FROM tags WHERE id = $1", tag_id)
            is_users_tag = tag and tag["name"] == "Users"

            where_clauses = ["pt.tag_id = $1"]
            if not is_users_tag:
                where_clauses.append("p.owner_tg_id IS NULL")
            if active_only:
                where_clauses.append("p.is_active = TRUE")
            
            where = " WHERE " + " AND ".join(where_clauses)
            
            total_sql = f"""
                SELECT COUNT(*) FROM prompts p
                INNER JOIN prompt_tags pt ON pt.prompt_id = p.id
                {where}
            """
            total = await conn.fetchval(total_sql, tag_id)
            total = int(total or 0)
            
            offset = max(0, page) * per_page
            rows_sql = f"""
                SELECT p.* FROM prompts p
                INNER JOIN prompt_tags pt ON pt.prompt_id = p.id
                {where}
                ORDER BY p.id DESC
                LIMIT $2 OFFSET $3
            """
            rows = await conn.fetch(rows_sql, tag_id, per_page, offset)
            return list(rows), total

    MAIN_MENU_TAG_NAME = "Main Menu"

    async def list_prompts_main_menu(self, active_only: bool = True) -> list[asyncpg.Record]:
        async with self.pool.acquire() as conn:
            tag = await conn.fetchrow("SELECT id FROM tags WHERE name = $1", self.MAIN_MENU_TAG_NAME)
            if not tag:
                return []
            tag_id = int(tag["id"])
            
            where_clauses = ["pt.tag_id = $1", "p.owner_tg_id IS NULL"]
            if active_only:
                where_clauses.append("p.is_active = TRUE")
            
            where = " WHERE " + " AND ".join(where_clauses)
            sql = f"""
                SELECT p.* FROM prompts p
                INNER JOIN prompt_tags pt ON pt.prompt_id = p.id
                {where}
                ORDER BY p.id DESC
            """
            return await conn.fetch(sql, tag_id)

    async def get_state_value(self, key: str) -> Optional[str]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT value FROM bot_state WHERE key = $1", key)
            if not row:
                return None
            return row["value"]

    async def set_state_value(self, key: str, value: str) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO bot_state (key, value)
                VALUES ($1, $2)
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                """,
                key,
                value,
            )

    async def create_promo_code(
        self,
        code: str,
        credits_amount: int,
        max_uses: Optional[int],
        created_by: int,
    ) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO promo_codes (code, credits_amount, max_uses, created_by)
                VALUES ($1, $2, $3, $4)
                """,
                code,
                credits_amount,
                max_uses,
                created_by,
            )

    async def list_promo_codes(self) -> list[asyncpg.Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetch("SELECT * FROM promo_codes ORDER BY id DESC")

    async def get_promo_code_by_id(self, promo_id: int) -> Optional[asyncpg.Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetchrow("SELECT * FROM promo_codes WHERE id = $1", promo_id)

    async def update_promo_code(
        self,
        promo_id: int,
        code: str,
        credits_amount: int,
        max_uses: Optional[int],
    ) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE promo_codes
                SET code = $1,
                    credits_amount = $2,
                    max_uses = $3
                WHERE id = $4
                """,
                code,
                credits_amount,
                max_uses,
                promo_id,
            )

    async def delete_promo_code(self, promo_id: int) -> bool:
        async with self.pool.acquire() as conn:
            result = await conn.execute("DELETE FROM promo_codes WHERE id = $1", promo_id)
            return result.endswith("1")

    async def set_promo_active(self, promo_id: int, is_active: bool) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE promo_codes
                SET is_active = $1
                WHERE id = $2
                """,
                is_active,
                promo_id,
            )

    async def reset_promo_uses(self, promo_id: int) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE promo_codes
                SET uses_count = 0
                WHERE id = $1
                """,
                promo_id,
            )

    async def redeem_promo_code(self, code: str, user_tg_id: int) -> tuple[bool, str, int]:
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                promo = await conn.fetchrow(
                    """
                    SELECT *
                    FROM promo_codes
                    WHERE code = $1 AND is_active = TRUE
                    FOR UPDATE
                    """,
                    code,
                )
                if not promo:
                    return False, "Promo code is invalid or inactive.", 0

                if promo["max_uses"] is not None and promo["uses_count"] >= promo["max_uses"]:
                    return False, "Promo code usage limit reached.", 0

                already_used = await conn.fetchrow(
                    """
                    SELECT 1
                    FROM promo_redemptions
                    WHERE promo_id = $1 AND user_tg_id = $2
                    """,
                    promo["id"],
                    user_tg_id,
                )
                if already_used:
                    return False, "You have already used this promo code.", 0

                await conn.execute(
                    """
                    INSERT INTO promo_redemptions (promo_id, user_tg_id)
                    VALUES ($1, $2)
                    """,
                    promo["id"],
                    user_tg_id,
                )
                await conn.execute(
                    """
                    UPDATE promo_codes
                    SET uses_count = uses_count + 1
                    WHERE id = $1
                    """,
                    promo["id"],
                )
                row = await conn.fetchrow(
                    """
                    UPDATE users
                    SET balance_tokens = balance_tokens + $1
                    WHERE tg_id = $2
                    RETURNING balance_tokens
                    """,
                    int(promo["credits_amount"]),
                    user_tg_id,
                )
                new_balance = int(row["balance_tokens"] or 0) if row else 0
                return True, "Promo code applied.", int(promo["credits_amount"])
