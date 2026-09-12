from pydantic import BaseModel, Field
from typing import List, Optional

class Candle(BaseModel):
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float

class OrderBook(BaseModel):
    bids: List[List[float]] = Field(description="List of [price, amount]")
    asks: List[List[float]] = Field(description="List of [price, amount]")
    timestamp: int

class SignalScore(BaseModel):
    symbol: str
    action: str = Field(..., pattern="^(SHORT|NEUTRAL)$")
    confidence_score: float = Field(ge=0.0, le=1.0)
    reasoning: str
    cross_exchange_validated: bool = False
