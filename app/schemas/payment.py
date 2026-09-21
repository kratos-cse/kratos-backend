import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import PaymentStatus, PaymentType


class CreateOrderRequest(BaseModel):
    """Exactly one of these two identifies what's being paid for.

    registration_id: for SOLO_REGISTRATION or TEAM_REGISTRATION — the row
      POST /events/{event_id}/registrations returns on the Authentication
      branch. payment_type and event_id are derived from it, never taken
      from the client.
    team_member_id: for TEAM_MEMBER_TOPUP — a member who joined an existing
      team via invite and is PENDING_PAYMENT (no registration row exists
      for them).
    """

    registration_id: Optional[uuid.UUID] = None
    team_member_id: Optional[uuid.UUID] = None

    @model_validator(mode="after")
    def exactly_one_target(self) -> "CreateOrderRequest":
        if bool(self.registration_id) == bool(self.team_member_id):
            raise ValueError("Provide exactly one of registration_id or team_member_id")
        return self


class CreateOrderResponse(BaseModel):
    payment_id: uuid.UUID
    razorpay_order_id: str
    amount_paise: int
    currency: str
    razorpay_key_id: str


class VerifyPaymentRequest(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


class VerifyPaymentResponse(BaseModel):
    payment_id: uuid.UUID
    applied: bool
    status: PaymentStatus


class PaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    payer_profile_id: uuid.UUID
    payment_type: PaymentType
    team_member_id: Optional[uuid.UUID]
    razorpay_order_id: str
    razorpay_payment_id: Optional[str]
    amount_paise: int
    currency: str
    status: PaymentStatus
    refund_id: Optional[str]
    refund_amount_paise: Optional[int]
    refunded_at: Optional[datetime]
    refund_reason: Optional[str]
    created_at: datetime
    updated_at: datetime


class PaymentListResponse(BaseModel):
    payments: list[PaymentOut]
    page: int
    page_size: int
    total: int


class RefundRequest(BaseModel):
    reason: str = Field(min_length=1)


class RefundResponse(BaseModel):
    payment_id: uuid.UUID
    refund_id: str
    refund_amount_paise: int
