import autogen
from typing import TypedDict, Any
from src.logger import logger

class CostSummary(TypedDict):
    total_cost: float | None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None

def normalize_usage_block(usage: Any) -> dict[str, Any] | None:
    if not isinstance(usage, dict):
        return None

    normalized: dict[str, Any] = {}
    total_cost = usage.get("total_cost")
    if isinstance(total_cost, (int, float)):
        normalized["total_cost"] = float(total_cost)

    for model_name, model_usage in usage.items():
        if model_name == "total_cost" or not isinstance(model_usage, dict):
            continue

        cleaned_usage = {
            key: value
            for key, value in model_usage.items()
            if key in {"cost", "prompt_tokens", "completion_tokens", "total_tokens"}
            and isinstance(value, (int, float))
        }
        if cleaned_usage:
            normalized[model_name] = cleaned_usage

    return normalized or None

def get_cost_summary(usage: Any, price: tuple[float, float] | None = None) -> CostSummary | None:
    if not isinstance(usage, dict):
        return None
    total_cost = usage.get("total_cost", 0)
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0

    for model_usage in usage.values():
        if not isinstance(model_usage, dict):
            continue
        prompt_tokens += model_usage.get("prompt_tokens", 0)
        completion_tokens += model_usage.get("completion_tokens", 0)
        total_tokens += model_usage.get("total_tokens", 0)

    if price is not None:
        # AG2's Responses client reports token counts but no token cost. Its
        # custom `price` option expects Chat Completions usage fields, so price
        # these counts after the conversation instead.
        total_cost = (prompt_tokens * price[0] + completion_tokens * price[1]) / 1000
  
    return {
        "total_cost": total_cost,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }

def extract_cost_summary(
    cost: Any, price: tuple[float, float] | None = None
) -> dict[str, CostSummary | None] | None:
    if isinstance(cost, dict):
        including_cache = get_cost_summary(cost.get("usage_including_cached_inference"), price)
        excluding_cache = get_cost_summary(cost.get("usage_excluding_cached_inference"), price)
        if including_cache or excluding_cache:
            return {
                "total_usage": including_cache,
                "actual_usage": excluding_cache,
            }

    return None

def merge_usage_reports(*usage_reports: dict[str, CostSummary | None] | None) -> dict[str, CostSummary | None] | None:
    """Aggregate multiple ``extract_cost_summary`` results into the same shape."""
    summary_keys = ("total_usage", "actual_usage")
    field_keys = ("total_cost", "prompt_tokens", "completion_tokens", "total_tokens")
    merged: dict[str, CostSummary | None] = {}

    for summary_key in summary_keys:
        combined_block: CostSummary = {
            "total_cost": None,
            "prompt_tokens": None,
            "completion_tokens": None,
            "total_tokens": None,
        }
        has_values = False

        for field_key in field_keys:
            values = [
                report[summary_key][field_key]
                for report in usage_reports
                if report
                and isinstance(report.get(summary_key), dict)
                and report[summary_key].get(field_key) is not None
            ]
            if not values:
                continue

            total = sum(values)
            combined_block[field_key] = round(total, 6) if field_key == "total_cost" else int(total)
            has_values = True

        if has_values:
            merged[summary_key] = combined_block

    return merged or None
