"""
x402 Payment Protocol Client

Enables USDC/USDT micropayments via HTTP 402.
Based on EIP-3009 (TransferWithAuthorization).

Flow:
1. Client makes request to x402-enabled endpoint
2. Server returns 402 with payment requirements
3. Client signs TransferWithAuthorization (EIP-712)
4. Client retries with X-Payment header containing signed authorization
5. Server verifies signature, executes transfer, returns response
"""

from __future__ import annotations

import base64
import json
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

import httpx

from joyhousebot.financial.chains import ChainConfig, SupportedChains, TokenInfo
from joyhousebot.financial.eip712 import (
    sign_transfer_with_authorization,
    sign_token_payment,
)
from joyhousebot.financial.token_balance import TokenBalanceChecker
from joyhousebot.identity.evm import EvmIdentity
from joyhousebot.identity.wallet_store import decrypt_wallet, get_wallet_address

logger = logging.getLogger(__name__)


@dataclass
class PaymentRequirement:
    """Payment requirement from 402 response"""
    scheme: str
    network: str
    max_amount_required: str
    pay_to_address: str
    required_deadline_seconds: int
    usdc_address: str
    usdt_address: Optional[str] = None
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Optional[PaymentRequirement]:
        """Parse from API response"""
        try:
            network = data.get("network", "")
            normalized = SupportedChains.normalize_network_id(network)
            if not normalized:
                return None
            
            max_amount = data.get("maxAmountRequired") or data.get("max_amount_required")
            if not max_amount:
                return None
            
            pay_to = data.get("payToAddress") or data.get("pay_to") or data.get("payTo")
            if not pay_to:
                return None
            
            usdc_address = data.get("usdcAddress") or data.get("asset") or data.get("usdc_address")
            
            deadline = data.get("requiredDeadlineSeconds") or data.get("required_deadline_seconds")
            if deadline is None:
                deadline = data.get("maxTimeoutSeconds") or 300
            
            return cls(
                scheme=data.get("scheme", "exact"),
                network=normalized,
                max_amount_required=str(max_amount),
                pay_to_address=pay_to,
                required_deadline_seconds=int(deadline),
                usdc_address=usdc_address or "",
                usdt_address=data.get("usdtAddress"),
            )
        except Exception as e:
            logger.error(f"Failed to parse payment requirement: {e}")
            return None


@dataclass
class ParsedPaymentRequired:
    """Parsed 402 response"""
    x402_version: int
    requirement: PaymentRequirement


@dataclass
class X402PaymentResult:
    """Result of x402 payment request"""
    success: bool
    response: Optional[Any] = None
    error: Optional[str] = None
    status: Optional[int] = None
    amount_paid: Optional[float] = None
    transaction_hash: Optional[str] = None


@dataclass
class X402Policy:
    """Payment policy constraints"""
    max_single_payment_cents: int = 100
    max_hourly_spend_cents: int = 500
    max_daily_spend_cents: int = 1000
    allowed_domains: List[str] = field(default_factory=lambda: ["*"])
    require_confirmation_above_cents: int = 50
    default_deadline_seconds: int = 300
    preferred_token: str = "USDC"
    preferred_network: str = "eip155:8453"


class X402Client:
    """
    x402 Payment Protocol Client
    
    Handles HTTP 402 payment flow with USDC/USDT on supported chains.
    """
    
    def __init__(
        self,
        policy: Optional[X402Policy] = None,
        http_client: Optional[httpx.AsyncClient] = None,
        balance_checker: Optional[TokenBalanceChecker] = None,
    ):
        """
        Initialize x402 client.
        
        Args:
            policy: Payment policy constraints
            http_client: Custom HTTP client
            balance_checker: Custom balance checker
        """
        self.policy = policy or X402Policy()
        self._http_client = http_client
        self._balance_checker = balance_checker
        self._hourly_spend: float = 0
        self._daily_spend: float = 0
        self._last_hour_reset: float = time.time()
        self._last_day_reset: float = time.time()
    
    async def _get_http_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client"""
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=30.0)
        return self._http_client
    
    async def _get_balance_checker(self) -> TokenBalanceChecker:
        """Get or create balance checker"""
        if self._balance_checker is None:
            self._balance_checker = TokenBalanceChecker()
        return self._balance_checker
    
    async def close(self) -> None:
        """Close resources"""
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None
        if self._balance_checker:
            await self._balance_checker.close()
            self._balance_checker = None
    
    def _reset_spend_counters(self) -> None:
        """Reset spend counters if time windows expired"""
        now = time.time()
        
        if now - self._last_hour_reset >= 3600:
            self._hourly_spend = 0
            self._last_hour_reset = now
        
        if now - self._last_day_reset >= 86400:
            self._daily_spend = 0
            self._last_day_reset = now
    
    def _check_spend_limits(self, amount_cents: float) -> tuple[bool, str]:
        """Check if payment is within spend limits"""
        self._reset_spend_counters()
        
        if amount_cents > self.policy.max_single_payment_cents:
            return False, f"Payment {amount_cents}¢ exceeds single limit {self.policy.max_single_payment_cents}¢"
        
        if self._hourly_spend + amount_cents > self.policy.max_hourly_spend_cents:
            return False, f"Payment would exceed hourly limit {self.policy.max_hourly_spend_cents}¢"
        
        if self._daily_spend + amount_cents > self.policy.max_daily_spend_cents:
            return False, f"Payment would exceed daily limit {self.policy.max_daily_spend_cents}¢"
        
        return True, ""
    
    def _record_spend(self, amount_cents: float) -> None:
        """Record successful payment for spend tracking"""
        self._hourly_spend += amount_cents
        self._daily_spend += amount_cents
    
    def _is_domain_allowed(self, url: str) -> bool:
        """Check if domain is in allowed list"""
        if "*" in self.policy.allowed_domains:
            return True
        
        from urllib.parse import urlparse
        domain = urlparse(url).netloc.lower()
        
        for allowed in self.policy.allowed_domains:
            if domain == allowed or domain.endswith("." + allowed):
                return True
        
        return False
    
    def parse_max_amount(self, max_amount: str, x402_version: int) -> int:
        """
        Parse maxAmountRequired to atomic units.
        
        For x402 v1: amount in dollars (e.g., "0.05" -> 50000 atomic units for USDC)
        For x402 v2+: amount in atomic units directly
        """
        amount = max_amount.strip()
        
        if not amount.replace(".", "").isdigit():
            raise ValueError(f"Invalid maxAmountRequired: {max_amount}")
        
        if "." in amount:
            dollars = float(amount)
            return int(dollars * 1_000_000)
        
        if x402_version >= 2 or len(amount) > 6:
            return int(amount)
        
        return int(float(amount) * 1_000_000)
    
    async def check_x402(self, url: str) -> Optional[PaymentRequirement]:
        """
        Check if URL requires x402 payment.
        
        Makes a HEAD request to check for 402 response.
        Returns payment requirement if 402, None otherwise.
        """
        client = await self._get_http_client()
        
        try:
            resp = await client.head(url, follow_redirects=True)
            if resp.status_code != 402:
                return None
            
            parsed = await self._parse_payment_required(resp)
            return parsed.requirement if parsed else None
        except Exception as e:
            logger.error(f"check_x402 failed: {e}")
            return None
    
    async def _parse_payment_required(
        self,
        response: httpx.Response,
    ) -> Optional[ParsedPaymentRequired]:
        """Parse 402 response to get payment requirements"""
        header = response.headers.get("X-Payment-Required")
        
        if header:
            try:
                raw = json.loads(header)
                normalized = self._normalize_payment_required(raw)
                if normalized:
                    return normalized
            except json.JSONDecodeError:
                pass
            
            try:
                decoded = base64.b64decode(header).decode("utf-8")
                raw = json.loads(decoded)
                normalized = self._normalize_payment_required(raw)
                if normalized:
                    return normalized
            except Exception:
                pass
        
        try:
            body = response.json()
            return self._normalize_payment_required(body)
        except Exception:
            pass
        
        return None
    
    def _normalize_payment_required(
        self,
        raw: Any,
    ) -> Optional[ParsedPaymentRequired]:
        """Normalize payment requirement from various formats"""
        if not isinstance(raw, dict):
            return None
        
        accepts = raw.get("accepts", [])
        if not isinstance(accepts, list) or not accepts:
            accepts = [raw] if "scheme" in raw else []
        
        requirements = []
        for item in accepts:
            req = PaymentRequirement.from_dict(item)
            if req:
                requirements.append(req)
        
        if not requirements:
            return None
        
        x402_version = raw.get("x402Version", 1)
        if isinstance(x402_version, str):
            x402_version = int(x402_version) if x402_version.isdigit() else 1
        
        exact_req = next(
            (r for r in requirements if r.scheme == "exact"),
            requirements[0],
        )
        
        return ParsedPaymentRequired(
            x402_version=x402_version,
            requirement=exact_req,
        )
    
    async def fetch_with_payment(
        self,
        url: str,
        identity: EvmIdentity,
        method: str = "GET",
        body: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        max_payment_cents: Optional[int] = None,
    ) -> X402PaymentResult:
        """
        Fetch URL with automatic x402 payment.
        
        If endpoint returns 402, signs payment and retries.
        
        Args:
            url: Target URL
            identity: EVM identity for signing
            method: HTTP method
            body: Request body
            headers: Additional headers
            max_payment_cents: Maximum payment allowed (overrides policy)
        
        Returns:
            X402PaymentResult with response or error
        """
        if not self._is_domain_allowed(url):
            return X402PaymentResult(
                success=False,
                error=f"Domain not allowed by policy",
                status=403,
            )
        
        client = await self._get_http_client()
        
        request_headers = {"Content-Type": "application/json"}
        if headers:
            request_headers.update(headers)
        
        try:
            initial_resp = await client.request(
                method,
                url,
                content=body,
                headers=request_headers,
                follow_redirects=True,
            )
            
            if initial_resp.status_code != 402:
                data = self._parse_response(initial_resp)
                return X402PaymentResult(
                    success=initial_resp.is_success,
                    response=data,
                    status=initial_resp.status_code,
                )
            
            parsed = await self._parse_payment_required(initial_resp)
            if not parsed:
                return X402PaymentResult(
                    success=False,
                    error="Could not parse payment requirements",
                    status=402,
                )
            
            amount_atomic = self.parse_max_amount(
                parsed.requirement.max_amount_required,
                parsed.x402_version,
            )
            amount_cents = amount_atomic / 10_000
            
            effective_max = max_payment_cents or self.policy.max_single_payment_cents
            if amount_cents > effective_max:
                return X402PaymentResult(
                    success=False,
                    error=f"Payment {amount_cents:.2f}¢ exceeds max allowed {effective_max}¢",
                    status=402,
                )
            
            allowed, reason = self._check_spend_limits(amount_cents)
            if not allowed:
                return X402PaymentResult(
                    success=False,
                    error=reason,
                    status=402,
                )
            
            chain = SupportedChains.get_chain(parsed.requirement.network)
            if not chain:
                return X402PaymentResult(
                    success=False,
                    error=f"Unsupported network: {parsed.requirement.network}",
                    status=402,
                )
            
            token_address = parsed.requirement.usdc_address
            token_symbol = "USDC"
            
            if parsed.requirement.usdt_address and self.policy.preferred_token == "USDT":
                token_address = parsed.requirement.usdt_address
                token_symbol = "USDT"
            
            checker = await self._get_balance_checker()
            balance = await checker.get_balance(
                identity.address,
                parsed.requirement.network,
                token_symbol,
            )
            
            if not balance.ok:
                return X402PaymentResult(
                    success=False,
                    error=f"Failed to check balance: {balance.error}",
                    status=402,
                )
            
            if balance.balance * 100 < amount_cents:
                return X402PaymentResult(
                    success=False,
                    error=f"Insufficient {token_symbol} balance: ${balance.balance:.2f} < ${amount_cents/100:.2f}",
                    status=402,
                )
            
            payment = await self._sign_payment(
                identity,
                parsed.requirement,
                amount_atomic,
                chain,
                token_address,
                token_symbol,
            )
            
            if not payment:
                return X402PaymentResult(
                    success=False,
                    error="Failed to sign payment",
                    status=402,
                )
            
            payment_header = base64.b64encode(
                json.dumps(payment).encode("utf-8")
            ).decode("ascii")
            
            paid_resp = await client.request(
                method,
                url,
                content=body,
                headers={
                    **request_headers,
                    "X-Payment": payment_header,
                },
                follow_redirects=True,
            )
            
            if paid_resp.is_success:
                self._record_spend(amount_cents)
            
            data = self._parse_response(paid_resp)
            
            return X402PaymentResult(
                success=paid_resp.is_success,
                response=data,
                status=paid_resp.status_code,
                amount_paid=amount_cents / 100,
            )
            
        except Exception as e:
            logger.error(f"x402 fetch failed: {e}")
            return X402PaymentResult(
                success=False,
                error=str(e),
            )
    
    def _parse_response(self, response: httpx.Response) -> Any:
        """Parse response body"""
        try:
            return response.json()
        except Exception:
            return response.text
    
    async def _sign_payment(
        self,
        identity: EvmIdentity,
        requirement: PaymentRequirement,
        amount_atomic: int,
        chain: ChainConfig,
        token_address: str,
        token_symbol: str = "USDC",
    ) -> Optional[Dict[str, Any]]:
        """Sign payment authorization for USDC or USDT"""
        try:
            now = int(time.time())
            valid_after = now - 60
            valid_before = now + requirement.required_deadline_seconds
            
            nonce = secrets.token_bytes(32)
            
            signed = sign_token_payment(
                identity=identity,
                token_address=token_address,
                token_symbol=token_symbol,
                chain_id=chain.chain_id,
                from_address=identity.address,
                to_address=requirement.pay_to_address,
                value=amount_atomic,
                valid_after=valid_after,
                valid_before=valid_before,
                nonce=nonce,
            )
            
            return {
                "x402Version": 1,
                "scheme": requirement.scheme,
                "network": requirement.network,
                "token": token_symbol,
                "payload": signed,
            }
        except Exception as e:
            logger.error(f"Failed to sign payment: {e}")
            return None


async def x402_fetch(
    url: str,
    identity: EvmIdentity,
    method: str = "GET",
    body: Optional[str] = None,
    headers: Optional[Dict[str, str]] = None,
    max_payment_cents: Optional[int] = None,
    policy: Optional[X402Policy] = None,
) -> X402PaymentResult:
    """
    Convenience function for x402 fetch.
    
    Creates a temporary client, makes the request, and cleans up.
    """
    client = X402Client(policy=policy)
    try:
        return await client.fetch_with_payment(
            url=url,
            identity=identity,
            method=method,
            body=body,
            headers=headers,
            max_payment_cents=max_payment_cents,
        )
    finally:
        await client.close()
