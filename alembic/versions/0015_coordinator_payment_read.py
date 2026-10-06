"""Grant payment-read to EVENT COORDINATOR role (read-only scoped payments)."""
from alembic import op
import sqlalchemy as sa
import uuid

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

EVENT_COORDINATOR_ROLE_ID = "00000000-0000-4000-8000-000000000003"
PERMISSION_KEY = "payment-read"


def upgrade() -> None:
    op.execute(
        sa.text(
            "INSERT INTO permissions (id, role_id, permission_key) "
            "SELECT CAST(:id AS uuid), CAST(:role_id AS uuid), :key "
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM permissions "
            "  WHERE role_id = CAST(:role_id AS uuid) AND permission_key = :key"
            ")"
        ).bindparams(id=str(uuid.uuid4()), role_id=EVENT_COORDINATOR_ROLE_ID, key=PERMISSION_KEY)
    )


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM permissions WHERE role_id = CAST(:role_id AS uuid) AND permission_key = :key").bindparams(
            role_id=EVENT_COORDINATOR_ROLE_ID, key=PERMISSION_KEY
        )
    )
