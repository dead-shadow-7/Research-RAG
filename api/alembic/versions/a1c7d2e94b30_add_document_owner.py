"""add document owner

Introduces tenancy. `documents.owner_id` is the Supabase user id from the token's `sub`
claim, and the same value names this tenant's Pinecone namespace.

**This migration deletes every existing document.** That is intentional rather than
careless. The column has to be NOT NULL -- a nullable owner is a row that every tenant
query has to remember to exclude, which is a leak waiting for the one query that forgets.
Adding a NOT NULL column to a populated table fails, and the pre-tenancy rows have no
owner to backfill from, so they go. Chunks and jobs cascade.

Clear Pinecone to match, or the old vectors sit in the default namespace forever --
invisible to the app, and still counting against the storage quota:

    python -c "from app.vectorstore import get_index; get_index().delete(delete_all=True)"

Revision ID: a1c7d2e94b30
Revises: b8fc1415232f
Create Date: 2026-09-21

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a1c7d2e94b30'
down_revision: Union[str, None] = 'b8fc1415232f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Chunks and ingest_jobs both cascade from documents.
    op.execute(sa.text("DELETE FROM documents"))

    op.add_column('documents', sa.Column('owner_id', sa.UUID(), nullable=False))
    op.create_index(op.f('ix_documents_owner_id'), 'documents', ['owner_id'], unique=False)

    # Dedupe is per-owner now, so the lookup is on the pair.
    op.drop_index(op.f('ix_documents_content_hash'), table_name='documents')
    op.create_index(
        'ix_documents_owner_hash', 'documents', ['owner_id', 'content_hash'], unique=False
    )


def downgrade() -> None:
    op.drop_index('ix_documents_owner_hash', table_name='documents')
    op.create_index(op.f('ix_documents_content_hash'), 'documents', ['content_hash'], unique=False)
    op.drop_index(op.f('ix_documents_owner_id'), table_name='documents')
    op.drop_column('documents', 'owner_id')
