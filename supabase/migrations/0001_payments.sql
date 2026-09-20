-- KRATOS'26 payments & refunds module
-- Owns: payments, receipts.
-- Reads without owning: profiles(id), events.fee, event_registration_rules.fee_charge_model,
--   registrations.payment_id, team_members(id, status).
-- Does NOT create profiles/events/registrations/team_members/teams — those belong to other tracks.

create table if not exists payments (
    id                   uuid primary key default gen_random_uuid(),
    payer_profile_id     uuid not null references profiles(id),
    payment_type         text not null check (payment_type in ('TEAM_REGISTRATION','SOLO_REGISTRATION','TEAM_MEMBER_TOPUP')),
    team_member_id       uuid null references team_members(id),  -- set only for TEAM_MEMBER_TOPUP
    razorpay_order_id    text unique not null,
    razorpay_payment_id  text unique null,                        -- set once Razorpay confirms capture
    amount_paise         bigint not null,                         -- integer paise, never a float rupee amount
    currency             text not null default 'INR',
    status               text not null default 'CREATED' check (status in ('CREATED','PAID','FAILED','REFUNDED')),
    refund_id            text null,
    refund_amount_paise  bigint null,
    refunded_at          timestamptz null,
    refund_reason        text null,
    created_at           timestamptz not null default now(),
    updated_at           timestamptz not null default now()
);

create table if not exists receipts (
    id             uuid primary key default gen_random_uuid(),
    payment_id     uuid unique not null references payments(id),  -- one receipt per payment
    receipt_number text unique not null,
    pdf_url        text not null,
    -- TODO(receipts-owner): pdf_url is NOT NULL per the brief's §2 schema, but PDF rendering may be
    -- owned by another track (§7). If this module inserts the row before a PDF exists, this column
    -- needs to become nullable — confirm ownership before relying on NOT NULL here.
    issued_at      timestamptz not null default now()
);

create index if not exists payments_payer_profile_id_idx on payments (payer_profile_id);
create index if not exists payments_status_idx on payments (status);
create index if not exists payments_razorpay_order_id_idx on payments (razorpay_order_id);
create index if not exists payments_razorpay_payment_id_idx on payments (razorpay_payment_id);
create index if not exists payments_team_member_id_idx on payments (team_member_id);

create or replace function payments_set_updated_at()
returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

drop trigger if exists payments_updated_at on payments;
create trigger payments_updated_at
  before update on payments
  for each row execute function payments_set_updated_at();

-- RLS: every route in this module runs with the service role, which bypasses RLS entirely.
-- These policies only govern any future direct client access and default to deny-all-writes.
alter table payments enable row level security;
alter table receipts enable row level security;

drop policy if exists payments_select_own on payments;
create policy payments_select_own on payments
  for select
  using (payer_profile_id = auth.uid());

drop policy if exists receipts_select_own on receipts;
create policy receipts_select_own on receipts
  for select
  using (
    exists (
      select 1 from payments
      where payments.id = receipts.payment_id
        and payments.payer_profile_id = auth.uid()
    )
  );

-- No insert/update/delete policies: writes only ever happen via the service-role backend.
