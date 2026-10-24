"""Token authority, program and extension checks (static analysis).

Limitation: static checks cannot prove a token is not a honeypot. They only
look for known privileged controls. Unknown extensions raise a warning.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Tuple

from ..models.common import CheckResult, DataLabel, Finding, Severity
from .policy import SecurityPolicy, unusable_reason

DANGEROUS_EXTENSIONS = frozenset({
    "permanent_delegate", "non_transferable", "transfer_hook",
    "default_account_state_frozen", "pausable",
})
BENIGN_EXTENSIONS = frozenset({
    "metadata_pointer", "token_metadata", "group_pointer", "group_member_pointer",
    "immutable_owner", "memo_transfer", "cpi_guard", "transfer_fee_config",
})
LIMITS = ("Static inspection of on-chain configuration only; it does not prove the token "
          "cannot restrict selling in other ways.")


@dataclass(frozen=True)
class TokenInfo:
    mint: str
    program: Optional[str]  # "spl-token" | "token-2022" | None (unknown)
    authorities_known: bool
    mint_authority: Optional[str]  # None = disabled (meaningful only if authorities_known)
    freeze_authority: Optional[str]
    extensions: Optional[Tuple[str, ...]]  # None = unknown
    transfer_fee_bps: Optional[int]
    transfer_fee_authority: Optional[str]
    fetched_at: Optional[datetime]
    provider: str
    label: DataLabel


def _unknown(reason: str, ts: Optional[datetime]) -> List[Finding]:
    return [Finding("token_authorities", CheckResult.UNKNOWN, Severity.CRITICAL, reason, ts, LIMITS)]


def check_token(info: Optional[TokenInfo], policy: SecurityPolicy, now: datetime) -> List[Finding]:
    if info is None:
        return _unknown("no token configuration data", None)
    bad = unusable_reason(policy, info.label, info.fetched_at, now)
    if bad:
        return _unknown(f"token data unusable: {bad}", info.fetched_at)
    ts = info.fetched_at
    if info.program not in ("spl-token", "token-2022"):
        return _unknown(f"token program unknown or unsupported: {info.program!r}", ts)
    if not info.authorities_known:
        return _unknown("mint/freeze authority state could not be read", ts)

    out: List[Finding] = []

    if info.mint_authority:
        fail = policy.block_on_mint_authority
        out.append(Finding("mint_authority", CheckResult.FAIL if fail else CheckResult.WARN,
                           Severity.CRITICAL if fail else Severity.WARNING,
                           f"mint authority is active ({info.mint_authority}); supply can be increased",
                           ts, LIMITS))
    else:
        out.append(Finding("mint_authority", CheckResult.PASS, Severity.INFO,
                           "mint authority disabled", ts, LIMITS))

    if info.freeze_authority:
        fail = policy.block_on_freeze_authority
        out.append(Finding("freeze_authority", CheckResult.FAIL if fail else CheckResult.WARN,
                           Severity.CRITICAL if fail else Severity.WARNING,
                           f"freeze authority is active ({info.freeze_authority}); accounts can be frozen",
                           ts, LIMITS))
    else:
        out.append(Finding("freeze_authority", CheckResult.PASS, Severity.INFO,
                           "freeze authority disabled", ts, LIMITS))

    if info.program == "spl-token":
        extensions: Optional[Tuple[str, ...]] = ()
    else:
        extensions = info.extensions
    if extensions is None:
        out.append(Finding("token_extensions", CheckResult.UNKNOWN, Severity.CRITICAL,
                           "token-2022 extensions could not be read", ts, LIMITS))
        return out

    problems: List[str] = []
    warnings: List[str] = []
    for ext in extensions:
        if ext in DANGEROUS_EXTENSIONS:
            problems.append(ext)
        elif ext not in BENIGN_EXTENSIONS:
            warnings.append(ext)
    if "transfer_fee_config" in extensions:
        if info.transfer_fee_bps is None:
            out.append(Finding("transfer_fee", CheckResult.UNKNOWN, Severity.CRITICAL,
                               "transfer fee extension present but fee unknown", ts, LIMITS))
        elif info.transfer_fee_bps > policy.max_transfer_fee_bps:
            out.append(Finding("transfer_fee", CheckResult.FAIL, Severity.CRITICAL,
                               f"transfer fee {info.transfer_fee_bps} bps exceeds {policy.max_transfer_fee_bps} bps",
                               ts, LIMITS))
        elif info.transfer_fee_bps > 0 and info.transfer_fee_authority:
            out.append(Finding("transfer_fee", CheckResult.WARN, Severity.WARNING,
                               f"transfer fee {info.transfer_fee_bps} bps; fee authority "
                               f"{info.transfer_fee_authority} can change it", ts, LIMITS))
        else:
            out.append(Finding("transfer_fee", CheckResult.PASS, Severity.INFO,
                               f"transfer fee {info.transfer_fee_bps} bps", ts, LIMITS))

    if problems:
        out.append(Finding("token_extensions", CheckResult.FAIL, Severity.CRITICAL,
                           "privileged or restrictive extensions: " + ", ".join(sorted(problems)), ts, LIMITS))
    elif warnings:
        out.append(Finding("token_extensions", CheckResult.WARN, Severity.WARNING,
                           "unreviewed extensions: " + ", ".join(sorted(warnings)), ts, LIMITS))
    else:
        out.append(Finding("token_extensions", CheckResult.PASS, Severity.INFO,
                           "no privileged extensions found", ts, LIMITS))
    return out
