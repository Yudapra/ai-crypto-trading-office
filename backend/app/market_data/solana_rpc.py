"""Solana JSON-RPC adapter: on-chain facts about a token mint.

What it provides (all read-only RPC methods, no wallet, no signing):
  * getAccountInfo (jsonParsed)  -> token program, mint/freeze authority, Token-2022 extensions, supply
  * getTokenAccountsByOwner      -> exact balance a given owner holds of the mint (developer holdings)

What it deliberately does NOT claim: a complete top-10 holder list. `getTokenLargestAccounts` returns only
the 20 largest token ACCOUNTS (not owners), so it cannot prove top-10 OWNER concentration; the live
provider therefore needs a holder source that can (see gmgn_api.py).

Field names follow the public Solana RPC documentation (jsonParsed account data). This adapter has been
tested against recorded-shape fixtures only, not against a live RPC node (the development sandbox had no
network access to Solana RPC hosts).
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Callable, Dict, Optional, Tuple

from ..models.common import DataLabel, utcnow
from ..holder_analysis.concentration import HolderAccount, HolderSnapshot
from ..security.token_checks import TokenInfo
from .base import ProviderUnavailable
from .http import JsonHttp

TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
_PROGRAM_BY_PARSED = {"spl-token": ("spl-token", TOKEN_PROGRAM), "spl-token-2022": ("token-2022", TOKEN_2022_PROGRAM)}

_EXTENSION_NAMES = {
    "metadataPointer": "metadata_pointer", "tokenMetadata": "token_metadata", "groupPointer": "group_pointer",
    "groupMemberPointer": "group_member_pointer", "transferFeeConfig": "transfer_fee_config",
    "nonTransferable": "non_transferable", "pausableConfig": "pausable", "pausable": "pausable",
}
_CAMEL = re.compile(r"(?<!^)(?=[A-Z])")


def _snake(name: str) -> str:
    return _CAMEL.sub("_", name).lower()


def map_extension(name: str, state: Optional[Dict[str, Any]]) -> Optional[str]:
    """Map a jsonParsed extension to the engine's vocabulary. None = inert / not worth reporting.

    Conservative on purpose: an extension is only treated as inert when it cannot ever become active.
    """
    state = state or {}
    if name == "permanentDelegate":
        return "permanent_delegate" if state.get("delegate") else None
    if name == "transferHook":
        return "transfer_hook" if (state.get("programId") or state.get("authority")) else None
    if name == "defaultAccountState":
        return "default_account_state_frozen" if state.get("accountState") == "frozen" else None
    return _EXTENSION_NAMES.get(name) or _snake(name)


def _as_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


class SolanaRpc:
    name = "solana-rpc"

    def __init__(self, url: str, http: JsonHttp, clock: Callable[[], datetime] = utcnow, timeout: float = 10.0):
        self._url = url
        self._http = http
        self._clock = clock
        self._timeout = timeout

    def _call(self, method: str, params: list) -> Any:
        resp = self._http.post_json(self._url, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                                    timeout=self._timeout)
        if not isinstance(resp, dict):
            raise ProviderUnavailable("invalid RPC response")
        if "error" in resp:
            code = resp["error"].get("code") if isinstance(resp["error"], dict) else None
            raise ProviderUnavailable(f"RPC error {code}")
        if "result" not in resp:
            raise ProviderUnavailable("invalid RPC response")
        return resp["result"]

    # ------------------------------------------------------------------
    def get_mint(self, mint: str) -> Tuple[TokenInfo, int]:
        """Return (TokenInfo, total supply in raw units) read from the mint account."""
        result = self._call("getAccountInfo", [mint, {"encoding": "jsonParsed", "commitment": "confirmed"}])
        value = result.get("value") if isinstance(result, dict) else None
        if not isinstance(value, dict):
            raise ProviderUnavailable("token mint account not found")
        data = value.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("parsed"), dict):
            raise ProviderUnavailable("mint account data could not be parsed")
        program = _PROGRAM_BY_PARSED.get(str(data.get("program")))
        if program is None or value.get("owner") != program[1]:
            raise ProviderUnavailable("account is not owned by a known token program")
        parsed = data["parsed"]
        info = parsed.get("info")
        if parsed.get("type") != "mint" or not isinstance(info, dict):
            raise ProviderUnavailable("account is not a token mint")
        supply = _as_int(info.get("supply"))
        if supply is None:
            raise ProviderUnavailable("mint supply is missing or invalid")

        known = "mintAuthority" in info and "freezeAuthority" in info
        extensions: Optional[Tuple[str, ...]] = ()
        fee_bps: Optional[int] = None
        fee_authority: Optional[str] = None
        if program[0] == "token-2022":
            raw_ext = info.get("extensions", [])
            if not isinstance(raw_ext, list):
                extensions = None
            else:
                names = set()
                for ext in raw_ext:
                    if not isinstance(ext, dict) or not isinstance(ext.get("extension"), str):
                        extensions = None
                        break
                    state = ext.get("state") if isinstance(ext.get("state"), dict) else {}
                    mapped = map_extension(ext["extension"], state)
                    if mapped:
                        names.add(mapped)
                    if ext["extension"] == "transferFeeConfig":
                        fee_bps, fee_authority = self._transfer_fee(state)
                else:
                    extensions = tuple(sorted(names))
        token = TokenInfo(
            mint=mint, program=program[0], authorities_known=known,
            mint_authority=info.get("mintAuthority") or None, freeze_authority=info.get("freezeAuthority") or None,
            extensions=extensions, transfer_fee_bps=fee_bps, transfer_fee_authority=fee_authority,
            fetched_at=self._clock(), provider=self.name, label=DataLabel.LIVE)
        return token, supply

    @staticmethod
    def _transfer_fee(state: Dict[str, Any]) -> Tuple[Optional[int], Optional[str]]:
        """Highest fee of the current and the scheduled one (conservative)."""
        values = []
        for key in ("olderTransferFee", "newerTransferFee"):
            fee = state.get(key)
            bps = _as_int(fee.get("transferFeeBasisPoints")) if isinstance(fee, dict) else None
            if bps is None:
                return None, None  # unreadable -> unknown (engine blocks)
            values.append(bps)
        return max(values), state.get("transferFeeConfigAuthority") or None

    def owner_balance(self, owner: str, mint: str) -> int:
        """Total raw balance of `mint` across all token accounts owned by `owner`."""
        result = self._call("getTokenAccountsByOwner",
                            [owner, {"mint": mint}, {"encoding": "jsonParsed", "commitment": "confirmed"}])
        value = result.get("value") if isinstance(result, dict) else None
        if not isinstance(value, list):
            raise ProviderUnavailable("invalid token account list")
        total = 0
        for item in value:
            try:
                info = item["account"]["data"]["parsed"]["info"]
                amount = _as_int(info["tokenAmount"]["amount"])
                same_mint = info["mint"] == mint
            except (KeyError, TypeError):
                raise ProviderUnavailable("invalid token account entry") from None
            if amount is None or not same_mint:
                raise ProviderUnavailable("invalid token account entry")
            total += amount
        return total

    def largest_holders(self, mint: str, supply: int) -> HolderSnapshot:
        """Owners of the 20 largest token ACCOUNTS. Always `complete=False`.

        `getTokenLargestAccounts` lists accounts, not owners, and only the top 20. An owner spread over many
        small accounts could still rank in the top 10 owners, so this can never prove the top-10 rule.
        """
        res = self._call("getTokenLargestAccounts", [mint, {"commitment": "confirmed"}])
        rows = res.get("value") if isinstance(res, dict) else None
        if not isinstance(rows, list) or not rows:
            raise ProviderUnavailable("invalid largest accounts response")
        addrs, amounts = [], []
        for row in rows[:20]:
            amount = _as_int(row.get("amount")) if isinstance(row, dict) else None
            if amount is None or not isinstance(row.get("address"), str):
                raise ProviderUnavailable("invalid largest accounts entry")
            addrs.append(row["address"])
            amounts.append(amount)
        infos = self._call("getMultipleAccounts", [addrs, {"encoding": "jsonParsed", "commitment": "confirmed"}])
        values = infos.get("value") if isinstance(infos, dict) else None
        if not isinstance(values, list) or len(values) != len(addrs):
            raise ProviderUnavailable("invalid account list")
        accounts = []
        for addr, amount, acc in zip(addrs, amounts, values):
            try:
                info = acc["data"]["parsed"]["info"]
                owner, same = info["owner"], info["mint"] == mint
            except (KeyError, TypeError):
                raise ProviderUnavailable("could not read token account owner") from None
            if not isinstance(owner, str) or not same:
                raise ProviderUnavailable("could not read token account owner")
            accounts.append(HolderAccount(addr, owner, amount, "unknown"))
        return HolderSnapshot(mint, supply, tuple(accounts), self._clock(), self.name, DataLabel.LIVE, False)
