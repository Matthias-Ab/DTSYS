"""Add push_tokens table and org_id to device_groups, scheduled_commands, notification_rules.

Revision ID: e8f9a0b1c2d3
Revises: d7f8e9a0b1c2
Create Date: 2026-04-14

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = 'e8f9a0b1c2d3'
down_revision = 'd7f8e9a0b1c2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # push_tokens table
    op.create_table(
        'push_tokens',
        sa.Column('id', sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column('user_id', sa.Uuid(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('token', sa.String(512), nullable=False, unique=True),
        sa.Column('platform', sa.String(10)),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('last_used', sa.DateTime(timezone=True)),
    )
    op.create_index('ix_push_tokens_user_id', 'push_tokens', ['user_id'])

    # Add org_id to device_groups
    op.add_column(
        'device_groups',
        sa.Column('org_id', sa.Uuid(as_uuid=True), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=True),
    )
    # Populate with first available org (migration helper — tighten via application logic)
    op.execute(
        "UPDATE device_groups SET org_id = (SELECT id FROM organizations LIMIT 1) WHERE org_id IS NULL"
    )
    op.alter_column('device_groups', 'org_id', nullable=False)
    op.drop_constraint('device_groups_name_key', 'device_groups', type_='unique')
    op.create_unique_constraint('uq_device_group_org_name', 'device_groups', ['org_id', 'name'])
    op.create_index('ix_device_groups_org_id', 'device_groups', ['org_id'])

    # Add org_id to scheduled_commands
    op.add_column(
        'scheduled_commands',
        sa.Column('org_id', sa.Uuid(as_uuid=True), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=True),
    )
    op.execute(
        "UPDATE scheduled_commands SET org_id = (SELECT id FROM organizations LIMIT 1) WHERE org_id IS NULL"
    )
    op.alter_column('scheduled_commands', 'org_id', nullable=False)
    op.create_index('ix_scheduled_commands_org_id', 'scheduled_commands', ['org_id'])

    # Add org_id to notification_rules
    op.add_column(
        'notification_rules',
        sa.Column('org_id', sa.Uuid(as_uuid=True), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=True),
    )
    op.execute(
        "UPDATE notification_rules SET org_id = (SELECT id FROM organizations LIMIT 1) WHERE org_id IS NULL"
    )
    op.alter_column('notification_rules', 'org_id', nullable=False)
    op.create_index('ix_notification_rules_org_id', 'notification_rules', ['org_id'])


def downgrade() -> None:
    op.drop_index('ix_notification_rules_org_id', 'notification_rules')
    op.drop_column('notification_rules', 'org_id')

    op.drop_index('ix_scheduled_commands_org_id', 'scheduled_commands')
    op.drop_column('scheduled_commands', 'org_id')

    op.drop_constraint('uq_device_group_org_name', 'device_groups', type_='unique')
    op.drop_index('ix_device_groups_org_id', 'device_groups')
    op.drop_column('device_groups', 'org_id')
    op.create_unique_constraint('device_groups_name_key', 'device_groups', ['name'])

    op.drop_index('ix_push_tokens_user_id', 'push_tokens')
    op.drop_table('push_tokens')
