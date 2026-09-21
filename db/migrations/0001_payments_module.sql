-- Payments & Refunds module — owns payments, receipts.
--
-- Uses TEXT + CHECK rather than native Postgres ENUM types deliberately:
-- other branches create Postgres ENUM types named payment_type/payment_status/etc.
-- If this migration and theirs both run against the same database, CREATE TYPE
-- would collide. Reconcile onto one schema owner at merge time.
--
-- NOTE ON FOREIGN KEYS:
-- payer_profile_id and team_member_id store UUID references to profiles(id)
-- and team_members(id). Explicit REFERENCES constraints are omitted here
-- so this standalone migration can run independently on fresh databases
-- before other branch tables exist. Real FK constraints are attached upon
-- consolidation into the main schema migration.

CREATE TABLE IF NOT EXISTS payments (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    payer_profile_id     UUID NOT NULL,                           -- references profiles(id) upon integration
    payment_type         TEXT NOT NULL CHECK (payment_type IN ('TEAM_REGISTRATION','SOLO_REGISTRATION','TEAM_MEMBER_TOPUP')),
    team_member_id       UUID NULL,                               -- references team_members(id) upon integration (TEAM_MEMBER_TOPUP only)
    razorpay_order_id    TEXT UNIQUE NOT NULL,
    razorpay_payment_id  TEXT UNIQUE NULL,                        -- set once Razorpay confirms capture
    amount_paise         BIGINT NOT NULL,                         -- integer paise, never a float rupee amount
    currency             TEXT NOT NULL DEFAULT 'INR',
    status               TEXT NOT NULL DEFAULT 'CREATED' CHECK (status IN ('CREATED','PAID','FAILED','REFUNDED')),
    refund_id            TEXT NULL,
    refund_amount_paise  BIGINT NULL,
    refunded_at          TIMESTAMPTZ NULL,
    refund_reason        TEXT NULL,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS receipts (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    payment_id     UUID UNIQUE NOT NULL REFERENCES payments(id),  -- one receipt per payment
    receipt_number TEXT UNIQUE NOT NULL,
    pdf_url        TEXT NOT NULL,
    issued_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS payments_payer_profile_id_idx ON payments (payer_profile_id);
CREATE INDEX IF NOT EXISTS payments_status_idx ON payments (status);
CREATE INDEX IF NOT EXISTS payments_razorpay_order_id_idx ON payments (razorpay_order_id);
CREATE INDEX IF NOT EXISTS payments_razorpay_payment_id_idx ON payments (razorpay_payment_id);
CREATE INDEX IF NOT EXISTS payments_team_member_id_idx ON payments (team_member_id);

CREATE OR REPLACE FUNCTION payments_set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS payments_updated_at ON payments;
CREATE TRIGGER payments_updated_at
  BEFORE UPDATE ON payments
  FOR EACH ROW EXECUTE FUNCTION payments_set_updated_at();
