"""item_categories: many-to-many vocabulary categories

Revision ID: b3c7d50e9a14
Revises: a7e81c3f2b05
Create Date: 2026-10-10

"""

import sqlalchemy as sa
from alembic import op

revision = 'b3c7d50e9a14'
down_revision = 'a7e81c3f2b05'
branch_labels = None
depends_on = None

# Frozen copy of app.categories.TOPIC_MAP. Inlined on purpose: a migration
# must keep producing the same result after the application's mapping moves on.
TOPIC_CATEGORIES = """
        ('basics', 'essentials'),
        ('body-health', 'health'),
        ('celebrations', 'leisure'),
        ('communication', 'essentials'),
        ('culture', 'leisure'),
        ('daily-routine', 'home'),
        ('describing', 'essentials'),
        ('directions', 'travel'),
        ('everyday-verbs', 'essentials'),
        ('food-drink', 'food'),
        ('hobbies-leisure', 'leisure'),
        ('home', 'home'),
        ('language-school', 'work-school'),
        ('meeting-people', 'essentials'),
        ('meeting-people', 'people'),
        ('nature-weather', 'nature'),
        ('people-family', 'people'),
        ('places-travel', 'travel'),
        ('shopping', 'work-school'),
        ('time-numbers', 'time-numbers')
"""


def upgrade() -> None:
    op.create_table(
        "item_categories",
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("item_id", "category"),
    )
    op.create_index(
        "ix_item_categories_category", "item_categories", ["category"], unique=False
    )

    # Backfill from the single-valued topic column so existing progress lands
    # in a category immediately; items with no topic go to essentials.
    op.execute(
        f"""
        INSERT INTO item_categories (item_id, category)
        SELECT i.id, m.category
        FROM items i
        JOIN (VALUES {TOPIC_CATEGORIES}) AS m(topic, category) ON m.topic = i.topic
        ON CONFLICT DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO item_categories (item_id, category)
        SELECT i.id, 'essentials'
        FROM items i
        WHERE NOT EXISTS (SELECT 1 FROM item_categories c WHERE c.item_id = i.id)
        ON CONFLICT DO NOTHING
        """
    )

    # The single-valued column has no remaining readers, and leaving it would
    # let the two disagree.
    op.drop_index("ix_items_topic", table_name="items")
    op.drop_column("items", "topic")


def downgrade() -> None:
    op.add_column("items", sa.Column("topic", sa.String(length=32), nullable=True))
    op.create_index("ix_items_topic", "items", ["topic"], unique=False)
    # Best effort: one category per item, so an item in two loses one.
    op.execute(
        """
        UPDATE items SET topic = sub.category
        FROM (
            SELECT item_id, MIN(category) AS category
            FROM item_categories GROUP BY item_id
        ) AS sub
        WHERE sub.item_id = items.id
        """
    )
    op.drop_index("ix_item_categories_category", table_name="item_categories")
    op.drop_table("item_categories")
