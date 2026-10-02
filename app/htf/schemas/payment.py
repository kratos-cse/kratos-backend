from typing import Optional
from pydantic import BaseModel, Field


class PaymentOrderOut(BaseModel):
    paymentId: str
    razorpayOrderId: str
    amountPaise: int
    currency: str
    razorpayKeyId: Optional[str] = None


class PaymentVerifyRequest(BaseModel):
    razorpay_order_id: str = Field(min_length=1)
    razorpay_payment_id: str = Field(min_length=1)
    razorpay_signature: str = Field(min_length=1)


class PaymentVerifyOut(BaseModel):
    status: str
    message: str
    outcome: Optional[str] = None
