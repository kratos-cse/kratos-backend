"""Leader-entered team members: store department on roster row."""

from alembic import op
import sqlalchemy as sa

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("team_members", sa.Column("department", sa.String(200), nullable=True))


def downgrade() -> None:
    op.drop_column("team_members", "department")
