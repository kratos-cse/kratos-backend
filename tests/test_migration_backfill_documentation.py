"""Document 0019 backfill semantics (static review)."""

BACKFILL_SQL = """
UPDATE payments p
SET registration_id = r.id
FROM registrations r
WHERE r.payment_id = p.id AND p.registration_id IS NULL
"""


def test_backfill_only_links_current_registration_pointer():
    """
    Historical note: backfill sets registration_id only where registrations.payment_id
    still points at that payment row (the latest attempt pointer at migration time).

    Older attempts after a retry may have registration_id NULL until set at create-order.
    Recovery must use payment.registration_id (post-0019) or explicit admin review —
    never payer identity alone.
    """
    assert "r.payment_id = p.id" in BACKFILL_SQL
    assert "registration_id IS NULL" in BACKFILL_SQL
