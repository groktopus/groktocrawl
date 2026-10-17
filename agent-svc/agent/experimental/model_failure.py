"""Sanitized, positively received model failures with replayable receipts."""

from typing import Any


class ModelOutputLimitError(ValueError):
    """A received length finish reason, not an unknown network outcome."""

    code = "model_output_limit"

    def __init__(
        self,
        *,
        http_status: Any = None,
        input_tokens: Any = None,
        output_tokens: Any = None,
    ) -> None:
        super().__init__("model transport failed: output limit reached")
        self.diagnostics: dict[str, Any] = {"finish_reason": "length"}
        if type(http_status) is int and 100 <= http_status <= 599:
            self.diagnostics["http_status"] = http_status
        for name, value in (
            ("input_tokens", input_tokens),
            ("output_tokens", output_tokens),
        ):
            if type(value) is int and 0 <= value <= 1_000_000_000:
                self.diagnostics[name] = value

    def receipt(self) -> dict[str, Any]:
        return {
            "model_failure": {"code": self.code, "diagnostics": dict(self.diagnostics)}
        }

    @classmethod
    def from_receipt(cls, output: dict[str, Any]) -> "ModelOutputLimitError":
        failure = output["model_failure"]
        if not isinstance(failure, dict) or failure.get("code") != cls.code:
            raise ValueError("invalid model failure receipt")
        diagnostics = failure.get("diagnostics")
        if (
            not isinstance(diagnostics, dict)
            or diagnostics.get("finish_reason") != "length"
        ):
            raise ValueError("invalid model failure diagnostics")
        return cls(
            **{
                key: diagnostics.get(key)
                for key in ("http_status", "input_tokens", "output_tokens")
            }
        )
