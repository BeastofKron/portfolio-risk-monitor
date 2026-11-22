"""
Black-Scholes Greeks — no external libraries.
All inputs are per-share / per-unit.
"""
import math
from typing import Literal


def _norm_cdf(x: float) -> float:
    """Cumulative distribution function for the standard normal distribution."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    """Probability density function for the standard normal distribution."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def black_scholes_greeks(
    option_type: Literal["call", "put"],
    spot: float,
    strike: float,
    dte: float,           # days to expiry (can be fractional)
    iv: float,            # implied volatility as decimal, e.g. 0.30
    risk_free_rate: float = 0.045,
) -> dict:
    """
    Compute Black-Scholes option price + Greeks.

    Returns a dict with keys:
        price, delta, gamma, theta (per day), vega (per 1-vol-point move)
    Returns zeros if the option is expired or IV/DTE are invalid.
    """
    if dte <= 0 or iv <= 0 or spot <= 0 or strike <= 0:
        return dict(price=0.0, delta=0.0, gamma=0.0, theta=0.0, vega=0.0)

    T = dte / 365.0
    sqrtT = math.sqrt(T)
    r = risk_free_rate

    d1 = (math.log(spot / strike) + (r + 0.5 * iv * iv) * T) / (iv * sqrtT)
    d2 = d1 - iv * sqrtT

    if option_type == "call":
        price = spot * _norm_cdf(d1) - strike * math.exp(-r * T) * _norm_cdf(d2)
        delta = _norm_cdf(d1)
        theta_raw = (
            -spot * _norm_pdf(d1) * iv / (2.0 * sqrtT)
            - r * strike * math.exp(-r * T) * _norm_cdf(d2)
        )
    else:  # put
        price = strike * math.exp(-r * T) * _norm_cdf(-d2) - spot * _norm_cdf(-d1)
        delta = _norm_cdf(d1) - 1.0
        theta_raw = (
            -spot * _norm_pdf(d1) * iv / (2.0 * sqrtT)
            + r * strike * math.exp(-r * T) * _norm_cdf(-d2)
        )

    gamma = _norm_pdf(d1) / (spot * iv * sqrtT)
    theta = theta_raw / 365.0          # per calendar day
    vega  = spot * _norm_pdf(d1) * sqrtT / 100.0  # per 1 vol-point (1%)

    return dict(
        price=round(price, 4),
        delta=round(delta, 6),
        gamma=round(gamma, 6),
        theta=round(theta, 6),
        vega=round(vega, 4),
    )
